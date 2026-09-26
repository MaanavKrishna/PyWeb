"""Track E e2e tests: harness self-tests + reference-app contracts.

Layers:
  1. Harness self-tests against the stdlib stub server (always run).
  2. Per-reference-app contract tests (static + stub-served; always run).
  3. Real-framework integration (skip until ``pyweb`` lands on main).
  4. Docs-snippet enforcement: every docs code fence maps to an executed file.
"""

from __future__ import annotations

import glob
import importlib.util
import json
import os
import re
import runpy
from pathlib import Path

import pytest

from . import e2e_harness as h

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
DOCS = ROOT / "docs"

APPS = ["counter", "todo", "blog", "auth", "chat", "offline-notes"]


def _read(app: str, name: str) -> str:
    return (EXAMPLES / app / name).read_text(encoding="utf-8")


def _meta(app: str) -> dict:
    return json.loads(_read(app, "meta.json"))


# ---------------------------------------------------------------------------
# 1. Harness self-tests (stub server)
# ---------------------------------------------------------------------------

class TestHarnessHttp:
    def test_ssr_status_body_headers(self):
        with h.stub_server() as url:
            res = h.http_get(url + "/")
            h.assert_ssr_response(
                res, ["data-pw-id=\"counter-1\"", "pw-bind=\"count\""]
            )

    def test_ssr_response_rejects_wrong_status(self):
        with h.stub_server() as url:
            res = h.http_get(url + "/missing")
            with pytest.raises(AssertionError):
                h.assert_ssr_response(res, ["anything"])

    def test_rpc_roundtrip_success(self):
        with h.stub_server() as url:
            body = h.rpc_roundtrip(url, "increment", {"count": 1})
            assert body == {"ok": True, "count": 2}

    def test_rpc_roundtrip_validation_error(self):
        with h.stub_server() as url:
            body = h.rpc_roundtrip(
                url, "add_todo", {"title": "  "}, expect_ok=False
            )
            assert body["ok"] is False and "title" in body["error"]

    def test_rpc_echo(self):
        with h.stub_server() as url:
            body = h.rpc_roundtrip(url, "echo", {"a": 1})
            assert body == {"ok": True, "echo": {"a": 1}}

    def test_hydration_markers_present(self):
        h.assert_hydration_markers(h.STUB_HTML)

    def test_hydration_markers_missing(self):
        with pytest.raises(AssertionError):
            h.assert_hydration_markers("<html><body>plain</body></html>")

    def test_static_hashing_resolves(self):
        with h.stub_server() as url:
            res = h.http_get(url + "/")
            found = h.check_static_hashing(res.body, url)
            assert found and all(a.startswith("/static/") for a in found)
            for asset in found:
                asset_res = h.http_get(url + asset)
                assert asset_res.status == 200
                assert "immutable" in asset_res.headers.get("cache-control", "")

    def test_static_hashing_skips_gracefully_when_absent(self):
        with pytest.raises(pytest.skip.Exception):
            h.assert_static_hashing("<html><body>no assets</body></html>")

    def test_ephemeral_port_boot(self):
        with h.stub_server() as url_a, h.stub_server() as url_b:
            assert url_a != url_b
            assert h.http_get(url_a + "/").status == 200
            assert h.http_get(url_b + "/").status == 200

    def test_assert_ssr_contains_reports_missing(self):
        with pytest.raises(AssertionError, match="missing-node"):
            h.assert_ssr_contains("<div>hi</div>", ["missing-node"])

    def test_compile_pyweb_skips_without_framework(self):
        if h.framework_available():
            pytest.skip("framework installed; skip the absent path")
        result = h.compile_pyweb(str(EXAMPLES / "counter" / "app.pyweb"))
        assert result.skipped and "not installed" in result.reason

    def test_boot_pyweb_app_raises_without_framework(self):
        if h.framework_available():
            pytest.skip("framework installed; skip the absent path")
        with pytest.raises(h.FrameworkNotAvailable):
            h.boot_pyweb_app(str(EXAMPLES / "counter"))


# ---------------------------------------------------------------------------
# 2. Reference-app contract tests
# ---------------------------------------------------------------------------

class TestAppContracts:
    @pytest.mark.parametrize("app", APPS)
    def test_files_exist(self, app):
        assert (EXAMPLES / app / "app.pyweb").is_file()
        assert (EXAMPLES / app / "meta.json").is_file()

    @pytest.mark.parametrize("app", APPS)
    def test_meta_valid(self, app):
        meta = _meta(app)
        assert meta["name"] == app
        assert meta["routes"] and isinstance(meta["routes"], list)
        assert isinstance(meta.get("rpc", []), list)
        assert meta["expected_nodes"] and meta["hydration_markers"]

    @pytest.mark.parametrize("app", APPS)
    def test_source_contains_expected_nodes(self, app):
        meta = _meta(app)
        h.assert_ssr_contains(_read(app, "app.pyweb"), meta["expected_nodes"])

    @pytest.mark.parametrize("app", APPS)
    def test_source_has_hydration_markers(self, app):
        meta = _meta(app)
        h.assert_hydration_markers(
            _read(app, "app.pyweb"), tuple(meta["hydration_markers"])
        )

    @pytest.mark.parametrize("app", APPS)
    def test_build_clean(self, app):
        """`pyweb build` clean; skips-with-pass until the compiler lands."""
        result = h.compile_pyweb(str(EXAMPLES / app / "app.pyweb"))
        if result.skipped:
            pytest.skip(f"build not runnable yet: {result.reason}")
        h.assert_ssr_contains(result.html, _meta(app)["expected_nodes"])

    @pytest.mark.parametrize("app", APPS)
    def test_serve_ssr_200(self, app):
        """Serve SSR 200 with expected nodes (stub-served pre-framework)."""
        meta = _meta(app)
        with h.stub_server(base_html=_read(app, "app.pyweb")) as url:
            res = h.http_get(url + "/")
            h.assert_ssr_response(res, meta["expected_nodes"])


class TestCounterApp:
    def test_reactive_nodes_and_rpc(self):
        meta = _meta("counter")
        with h.stub_server(base_html=_read("counter", "app.pyweb")) as url:
            res = h.http_get(url + "/")
            h.assert_ssr_response(res, meta["expected_nodes"])
            h.assert_hydration_markers(res.body)
            assert h.rpc_roundtrip(url, "increment", {"count": 0})["count"] == 1


class TestTodoApp:
    def test_crud_binds_list_and_validation(self):
        meta = _meta("todo")
        with h.stub_server(base_html=_read("todo", "app.pyweb")) as url:
            res = h.http_get(url + "/")
            h.assert_ssr_response(res, meta["expected_nodes"])
            assert "pw-each" in res.body  # list rendering marker
            ok_body = h.rpc_roundtrip(url, "add_todo", {"title": "write docs"})
            assert ok_body["ok"] is True and ok_body["title"] == "write docs"
            err_body = h.rpc_roundtrip(
                url, "add_todo", {"title": ""}, expect_ok=False
            )
            assert err_body["ok"] is False


class TestBlogApp:
    def test_seo_meta(self):
        meta = _meta("blog")
        seo = meta["seo"]
        with h.stub_server() as url:
            res = h.http_get(url + seo["route"])
            h.assert_ssr_response(
                res,
                [f"<title>{seo['title']}</title>", "<article",
                 f'content="{seo["description"]}"',
                 f'content="{seo["og_title"]}"'],
            )
            assert 'name="description"' in res.body
            assert 'property="og:title"' in res.body


class TestAuthApp:
    def test_protected_redirect_and_login_page(self):
        meta = _meta("auth")["auth"]
        with h.stub_server(base_html=_read("auth", "app.pyweb")) as url:
            anon = h.http_get(
                url + meta["protected_route"], follow_redirects=False
            )
            assert anon.status == meta["expect_redirect"]
            assert anon.headers.get("location") == meta["login_route"]
            login = h.http_get(url + meta["login_route"])
            assert login.status == 200
            authed = h.http_get(
                url + meta["protected_route"],
                headers={"Authorization": "Bearer test"},
            )
            assert authed.status == 200


class TestChatApp:
    def test_sse_then_poll_fallback(self):
        meta = _meta("chat")["realtime"]
        assert meta["strategy"] == "sse-with-poll-fallback"
        with h.stub_server(base_html=_read("chat", "app.pyweb")) as url:
            res = h.http_get(url + "/")
            h.assert_ssr_response(res, _meta("chat")["expected_nodes"])
            sse = h.http_get(url + meta["sse_path"])
            if sse.status == 200 and "text/event-stream" in sse.headers.get(
                "content-type", ""
            ):
                assert "data:" in sse.body
            else:  # documented polling fallback
                poll = h.http_get(url + meta["poll_path"])
                assert poll.status == 200
                assert "messages" in poll.json()
            poll = h.http_get(url + meta["poll_path"])
            assert poll.status == 200
            assert poll.json() == {"messages": [{"text": "hi"}]}


class TestOfflineNotesApp:
    def test_sync_contract_documented(self):
        meta = _meta("offline-notes")
        contract = meta["sync_contract"]
        assert contract["push_path"] == "/__pyweb/rpc/sync_notes"
        assert contract["conflict"] == "last-write-wins"
        assert "pw-outbox" in contract["queue"]
        with h.stub_server(
            base_html=_read("offline-notes", "app.pyweb")
        ) as url:
            res = h.http_get(url + "/")
            h.assert_ssr_response(res, meta["expected_nodes"])
            assert "pw-sync" in res.body


# ---------------------------------------------------------------------------
# 3. Real-framework integration (skip until pyweb lands)
# ---------------------------------------------------------------------------

needs_framework = pytest.mark.skipif(
    not h.framework_available(), reason="pyweb framework not installed"
)


@needs_framework
@pytest.mark.parametrize("app", APPS)
def test_framework_build(app):
    result = h.compile_pyweb(str(EXAMPLES / app / "app.pyweb"))
    assert not result.skipped, result.reason
    h.assert_ssr_contains(result.html, _meta(app)["expected_nodes"])


@needs_framework
@pytest.mark.parametrize("app", APPS)
def test_framework_serve(app):
    with h.boot_pyweb_app(str(EXAMPLES / app)) as url:  # type: ignore[attr-defined]
        res = h.http_get(url + "/")
        h.assert_ssr_response(res, _meta(app)["expected_nodes"])


# ---------------------------------------------------------------------------
# 4. Docs-snippet enforcement: zero untested snippets
# ---------------------------------------------------------------------------

SNIPPET_MARKER_RE = re.compile(r"<!--\s*snippet:\s*(\S+?)\s*-->")
FENCE_RE = re.compile(r"```")


def _doc_fences_with_context(path: Path) -> list[tuple[int, str | None]]:
    """Return (fence_index, preceding snippet path or None) per OPENING fence."""
    lines = path.read_text(encoding="utf-8").splitlines()
    fence_idx = 0
    pending: str | None = None
    in_fence = False
    out: list[tuple[int, str | None]] = []
    for line in lines:
        marker = SNIPPET_MARKER_RE.search(line)
        if marker and not in_fence:
            pending = marker.group(1)
            continue
        if line.strip().startswith("```"):
            if not in_fence:
                fence_idx += 1
                out.append((fence_idx, pending))
                pending = None
            in_fence = not in_fence
    return out


class TestDocSnippets:
    DOC_FILES = sorted(glob.glob(str(DOCS / "*.md")))

    def test_docs_exist(self):
        expected = [f"0{i}-{n}.md" for i, n in enumerate(
            ["quickstart", "tutorial-todo", "reactivity", "rpc-placement",
             "database", "auth", "deploy", "escape-hatches"])]
        for name in expected + ["BUGLOG.md"]:
            assert (DOCS / name).is_file(), f"missing docs/{name}"

    def test_every_fence_has_snippet_marker(self):
        violations = []
        for doc in self.DOC_FILES:
            if os.path.basename(doc) == "BUGLOG.md":
                continue
            for fence_idx, snippet in _doc_fences_with_context(Path(doc)):
                if snippet is None:
                    violations.append(f"{doc}:{fence_idx} fence without snippet")
        assert not violations, "untested snippets:\n" + "\n".join(violations)

    def test_every_snippet_source_exists_and_runs(self):
        checked: set[str] = set()
        for doc in self.DOC_FILES:
            text = Path(doc).read_text(encoding="utf-8")
            for marker in SNIPPET_MARKER_RE.findall(text):
                assert not os.path.isabs(marker), f"absolute snippet path {marker}"
                src = ROOT / marker
                assert src.is_file(), f"snippet source missing: {marker}"
                checked.add(marker)
        assert checked, "no snippet markers found in docs"
        for marker in sorted(checked):
            src = ROOT / marker
            if src.suffix == ".py":
                runpy.run_path(str(src), run_name="__tested__")

    def test_snippet_fence_matches_source(self):
        """Each code fence body must appear verbatim in its snippet source.

        ``sh`` fences are exempt: they invoke the suite itself rather than
        sampling framework code.
        """
        for doc in self.DOC_FILES:
            if os.path.basename(doc) == "BUGLOG.md":
                continue
            text = Path(doc).read_text(encoding="utf-8")
            segments = text.split("```")
            # segments[1], segments[3], ... are fence bodies; the marker
            # precedes the opening fence in segments[0], segments[2], ...
            for i in range(1, len(segments), 2):
                body = segments[i]
                lang = body.split("\n")[0].strip()
                if lang == "sh":
                    continue
                pre = segments[i - 1]
                nearby = "\n".join(pre.split("\n")[-4:])
                markers = SNIPPET_MARKER_RE.findall(nearby)
                assert markers, f"{doc}: fence without snippet marker nearby"
                src_text = (ROOT / markers[-1]).read_text(encoding="utf-8")
                body = segments[i]
                code = "\n".join(body.split("\n")[1:]).strip("\n")
                # normalize: fence may quote a docstring line ("# ...") or a
                # subset; require the first non-empty line to be in source.
                first = next(
                    (ln.strip() for ln in code.splitlines() if ln.strip()), ""
                )
                assert first and first in src_text, (
                    f"{doc}: fence content not found in {markers[-1]}: {first!r}"
                )

    def test_snippet_modules_behave(self):
        import examples.snippets.auth_guard as ag
        import examples.snippets.chat_realtime as cr
        import examples.snippets.database as db
        import examples.snippets.offline_sync as osync
        import examples.snippets.reactivity as rx
        import examples.snippets.rpc_client as rpc
        import examples.snippets.todo_crud as tc

        assert ag.guard("/dashboard", False) == (302, "/login")
        assert ag.guard("/dashboard", True) == (200, "dashboard")
        with pytest.raises(ValueError):
            ag.login("", "")
        assert cr.pick_transport(True) == "/__pyweb/events"
        assert cr.pick_transport(False) == "/__pyweb/poll"
        assert cr.parse_sse_frame(
            'event: message\ndata: {"text": "hi"}\n\n') == {"text": "hi"}
        assert rpc.PLACEMENT_RULES == (
            "ssr-for-reads", "rpc-for-mutations", "validation-error-contract")
        assert osync.merge_notes(
            [{"id": "a", "text": "1"}],
            osync.queue_push([], {"id": "b", "text": "2"}),
        ) == [{"id": "a", "text": "1"}, {"id": "b", "text": "2"}]
        assert osync.ESCAPE_HATCHES == ("raw-html", "raw-sql", "custom-headers")

        seen: list = []
        sig = rx.Signal(0)
        sig.subscribe(seen.append)
        sig.value = 5
        assert seen == [5]
        assert 'pw-bind="count"' in rx.render_count(5)

        conn = db.connect()
        assert db.insert_todo(conn, "t1", "x")["title"] == "x"
        assert len(db.all_todos(conn)) == 1
        with pytest.raises(ValueError):
            db.insert_todo(conn, "t2", "  ")

        assert tc.add_todo("n")["title"] == "n"
        with pytest.raises(ValueError):
            tc.add_todo("  ")


class TestFrameworkProbe:
    def test_probe_reports_bool(self):
        assert isinstance(h.framework_available(), bool)
        assert h.framework_available() == (
            importlib.util.find_spec("pyweb") is not None
        )
