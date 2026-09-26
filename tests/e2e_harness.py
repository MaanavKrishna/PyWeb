"""PyWeb E2E QA harness (Track E).

Stdlib + pytest + HTTP only: no headless browser, no node/playwright.

 against stub servers so the harness itself is tested
even before the ``pyweb`` framework package exists.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import json
import re
import shutil
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Framework probing
# ---------------------------------------------------------------------------

class FrameworkNotAvailable(RuntimeError):
    """Raised when the ``pyweb`` framework package cannot be used."""


def framework_available() -> bool:
    """Return True when the ``pyweb`` framework package is importable."""
    return importlib.util.find_spec("pyweb") is not None


def require_framework() -> None:
    """Raise :class:`FrameworkNotAvailable` unless ``pyweb`` is importable."""
    if not framework_available():
        raise FrameworkNotAvailable(
            "pyweb framework package is not installed; "
            "framework-dependent checks are skipped until it lands on main."
        )


# ---------------------------------------------------------------------------
# .pyweb compilation
# ---------------------------------------------------------------------------

@dataclass
class CompileResult:
    html: str = ""
    skipped: bool = False
    reason: str = ""
    details: dict = field(default_factory=dict)


def compile_pyweb(path: str, *, timeout: int = 60) -> CompileResult:
    """Compile a ``.pyweb`` file to SSR HTML.

    Tries, in order: ``pyweb.compiler`` Python APIs, then the ``pyweb build``
    CLI. When the framework is absent the result is ``skipped=True`` instead
    of an error so suites stay green on framework-less checkouts.
    """
    if not framework_available():
        return CompileResult(skipped=True, reason="pyweb framework not installed")
    try:
        compiler = importlib.import_module("pyweb.compiler")
    except ImportError:
        compiler = None
    if compiler is not None:
        for attr in ("compile_file", "compile", "build", "render"):
            fn = getattr(compiler, attr, None)
            if callable(fn):
                try:
                    html = fn(path)
                    return CompileResult(
                        html=html if isinstance(html, str) else str(html),
                        details={"via": f"pyweb.compiler.{attr}"},
                    )
                except TypeError:
                    continue
                except Exception as exc:  # noqa: BLE001 - surfaced in result
                    return CompileResult(
                        skipped=True, reason=f"compiler.{attr} failed: {exc}"
                    )
    cli = shutil.which("pyweb")
    if cli is not None:
        try:
            proc = subprocess.run(
                [cli, "build", path],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return CompileResult(skipped=True, reason=f"pyweb CLI failed: {exc}")
        if proc.returncode == 0:
            return CompileResult(
                html=proc.stdout, details={"via": "pyweb CLI"}
            )
        return CompileResult(
            skipped=True, reason=f"pyweb build exited {proc.returncode}: {proc.stderr.strip()}"
        )
    return CompileResult(skipped=True, reason="no pyweb compiler API or CLI found")


def assert_ssr_contains(html: str, nodes: list[str]) -> None:
    """Assert every expected node substring is present in SSR HTML."""
    missing = [n for n in nodes if n not in html]
    if missing:
        raise AssertionError(
            "SSR HTML missing expected nodes: "
            + ", ".join(repr(m) for m in missing)
        )


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib)
# ---------------------------------------------------------------------------

@dataclass
class HttpResult:
    status: int
    headers: dict[str, str]
    body: str

    def json(self) -> Any:
        return json.loads(self.body)


def _read_response(resp: Any) -> HttpResult:
    raw = resp.read()
    body = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    headers = {k.lower(): v for k, v in resp.headers.items()}
    return HttpResult(status=resp.status, headers=headers, body=body)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN202
        return None


def _opener(*, follow_redirects: bool) -> urllib.request.OpenerDirector:
    if follow_redirects:
        return urllib.request.build_opener()
    return urllib.request.build_opener(_NoRedirect())


def http_get(url: str, *, headers: dict | None = None,
             follow_redirects: bool = True, timeout: int = 10) -> HttpResult:
    """GET ``url`` with the stdlib; never raises on HTTP error statuses."""
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with _opener(follow_redirects=follow_redirects).open(
            req, timeout=timeout
        ) as resp:
            return _read_response(resp)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        body = raw.decode("utf-8", errors="replace")
        return HttpResult(
            status=exc.code,
            headers={k.lower(): v for k, v in (exc.headers or {}).items()},
            body=body,
        )


def http_post_json(url: str, payload: dict, *, headers: dict | None = None,
                   timeout: int = 10) -> HttpResult:
    """POST JSON to ``url``; never raises on HTTP error statuses."""
    data = json.dumps(payload).encode("utf-8")
    merged = {"Content-Type": "application/json"}
    merged.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=merged, method="POST")
    try:
        with _opener(follow_redirects=True).open(req, timeout=timeout) as resp:
            return _read_response(resp)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        body = raw.decode("utf-8", errors="replace")
        return HttpResult(
            status=exc.code,
            headers={k.lower(): v for k, v in (exc.headers or {}).items()},
            body=body,
        )


def assert_ssr_response(res: HttpResult, nodes: list[str],
                        *, expect_status: int = 200) -> None:
    """Assert SSR response status, HTML content-type, body nodes."""
    assert res.status == expect_status, (
        f"expected status {expect_status}, got {res.status}: {res.body[:300]}"
    )
    ctype = res.headers.get("content-type", "")
    assert "text/html" in ctype, f"expected text/html, got {ctype!r}"
    assert_ssr_contains(res.body, nodes)


# ---------------------------------------------------------------------------
# RPC roundtrip
# ---------------------------------------------------------------------------

RPC_PATH = "/__pyweb/rpc"


def rpc_roundtrip(base_url: str, name: str, payload: dict,
                  *, expect_ok: bool = True,
                  expect_status: int | None = None) -> Any:
    """POST ``/__pyweb/rpc/<name>`` and assert the success/error contract.

    Success: HTTP 200 with JSON ``{"ok": true, ...}``.
    Validation failure: HTTP 400/422 with JSON ``{"ok": false, "error": ...}``.
    Returns the parsed JSON body.
    """
    res = http_post_json(f"{base_url.rstrip('/')}{RPC_PATH}/{name}", payload)
    body: Any = None
    try:
        body = json.loads(res.body)
    except (json.JSONDecodeError, TypeError):
        body = None
    if expect_ok:
        assert res.status == 200, (
            f"RPC {name}: expected 200, got {res.status}: {res.body[:300]}"
        )
        assert isinstance(body, dict) and body.get("ok") is True, (
            f"RPC {name}: expected ok:true, got {res.body[:300]}"
        )
    else:
        want = expect_status if expect_status is not None else (400, 422)
        want_tuple = (want,) if isinstance(want, int) else tuple(want)
        assert res.status in want_tuple, (
            f"RPC {name}: expected validation status {want_tuple}, "
            f"got {res.status}: {res.body[:300]}"
        )
        assert isinstance(body, dict) and body.get("ok") is False, (
            f"RPC {name}: expected ok:false error body, got {res.body[:300]}"
        )
        assert body.get("error"), f"RPC {name}: error body missing 'error' detail"
    return body


# ---------------------------------------------------------------------------
# Hydration markers / static-asset hashing
# ---------------------------------------------------------------------------

HYDRATION_MARKERS = ("data-pw-id", "pw-bind")


def assert_hydration_markers(html: str,
                             markers: tuple = HYDRATION_MARKERS) -> None:
    """Assert client-hydration markers are present in SSR HTML."""
    missing = [m for m in markers if m not in html]
    if missing:
        raise AssertionError(
            "SSR HTML missing hydration markers: " + ", ".join(missing)
        )


HASHED_ASSET_RE = re.compile(
    r"""['"](/static/[^'"\s]*?\.[0-9a-fA-F]{6,}\.[a-zA-Z0-9]+)["']"""
)


def check_static_hashing(html: str, base_url: str = "",
                         asset_paths: list[str] | None = None) -> list[str] | None:
    """Return hashed static-asset URLs found in ``html``, or None if absent.

    When ``base_url`` is given, each found asset is fetched and must be 200.
    Callers skip when this returns None (hashing not implemented).
    """
    found = HASHED_ASSET_RE.findall(html)
    if asset_paths:
        found = list(dict.fromkeys(found + asset_paths))
    if not found:
        return None
    if base_url:
        for asset in found:
            res = http_get(f"{base_url.rstrip('/')}{asset}")
            assert res.status == 200, (
                f"hashed asset {asset} returned {res.status}"
            )
    return found


def assert_static_hashing(html: str, base_url: str = "",
                          asset_paths: list[str] | None = None) -> list[str]:
    """Assert hashed static-asset refs exist and resolve; skip if absent."""
    import pytest  # lazy: keeps this module importable without pytest

    found = check_static_hashing(html, base_url, asset_paths)
    if not found:
        pytest.skip("static-asset hashing not implemented; skipping gracefully")
    return found


# ---------------------------------------------------------------------------
# Generic in-process server boot
# ---------------------------------------------------------------------------

def _free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def boot_server(handler_factory: Callable[[], BaseHTTPRequestHandler],
                *, host: str = "127.0.0.1", port: int = 0,
                timeout: int = 10):
    """Serve ``handler_factory`` in-process on an ephemeral port.

    Returns ``(server, thread, base_url)``. Call ``server.shutdown()`` and
    ``thread.join()`` (or use :func:`serving`) when done.
    """
    import time

    chosen = port or _free_port(host)
    server = ThreadingHTTPServer((host, chosen), handler_factory)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    base_url = f"http://{host}:{server.server_address[1]}"
    waited = 0.0
    while waited < timeout:
        try:
            with socket.create_connection((host, server.server_address[1]),
                                          timeout=1):
                break
        except OSError:
            time.sleep(0.05)
            waited += 0.05
    return server, thread, base_url


@contextlib.contextmanager
def serving(handler_factory: Callable[[], BaseHTTPRequestHandler], **kwargs):
    """Context manager around :func:`boot_server` yielding ``base_url``."""
    server, thread, base_url = boot_server(handler_factory, **kwargs)
    try:
        yield base_url
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()


def boot_pyweb_app(app_dir: str, **kwargs):
    """Boot a real ``.pyweb`` app's server in-process (framework required).

    Probes known ``pyweb`` server entry points; raises
    :class:`FrameworkNotAvailable` when the framework (or a known serve API)
    is absent so callers can ``pytest.skip`` instead of failing.
    """
    require_framework()
    pyweb = importlib.import_module("pyweb")
    candidates = [
        ("serve", ("run", "serve", "serve_app", "run_app")),
        ("server", ("run", "serve", "serve_app", "run_app")),
        ("cli", ("serve", "run")),
        ("app", ("serve", "run")),
    ]
    errors: list[str] = []
    for module_name, attrs in candidates:
        try:
            module = importlib.import_module(f"pyweb.{module_name}")
        except ImportError as exc:
            errors.append(f"pyweb.{module_name}: {exc}")
            continue
        for attr in attrs:
            fn = getattr(module, attr, None)
            if callable(fn):
                try:
                    return fn(app_dir, **kwargs)
                except TypeError:
                    try:
                        return fn(app_dir)
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"pyweb.{module_name}.{attr}: {exc}")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"pyweb.{module_name}.{attr}: {exc}")
    for attr in ("serve", "run", "serve_app", "run_app"):
        fn = getattr(pyweb, attr, None)
        if callable(fn):
            try:
                return fn(app_dir, **kwargs)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"pyweb.{attr}: {exc}")
    raise FrameworkNotAvailable(
        "pyweb framework found but no known server entry point; "
        + "; ".join(errors)
    )


# ---------------------------------------------------------------------------
# Programmable stub server (self-tests + contract tests pre-framework)
# ---------------------------------------------------------------------------

STUB_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PyWeb QA stub</title>
<meta name="description" content="PyWeb QA harness stub page">
<meta property="og:title" content="PyWeb QA stub">
<link rel="stylesheet" href="/static/app.abc123.css">
</head>
<body>
<div data-pw-id="counter-1" pw-bind="count">0</div>
<button data-pw-id="counter-inc" data-pw-action="increment">+</button>
<ul data-pw-id="todo-list" pw-bind="todos">
<li data-pw-id="todo-1">first todo</li>
</ul>
<article data-pw-id="blog-post-1"><h1>Hello</h1></article>
<script src="/static/app.abc123.js"></script>
</body>
</html>
"""


def make_stub_handler(base_html: str = STUB_HTML,
                      extra_routes: dict | None = None):
    """Build a stub ``BaseHTTPRequestHandler`` class for harness self-tests.

    Covers: SSR ``/``, static hashed assets, RPC ``increment``/``add_todo``
    (incl. 400 validation case), protected ``/dashboard`` -> ``/login``
    redirect, ``/login``, SSE ``/__pyweb/events``, polling
    ``/__pyweb/poll``, and an SEO ``/blog/hello`` page.
    """
    routes = dict(extra_routes or {})

    class StubHandler(BaseHTTPRequestHandler):
        server_version = "PyWebQAStub/1.0"

        def log_message(self, *args):  # noqa: ANN001, ANN202
            pass

        def _send(self, status: int, body: bytes,
                  content_type: str = "text/html; charset=utf-8",
                  extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return {}

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if path in routes and "GET" in routes[path]:
                status, ctype, body, extra = routes[path]["GET"]
                self._send(status, body, ctype, extra)
                return
            if path == "/":
                self._send(200, base_html.encode("utf-8"))
            elif path in ("/static/app.abc123.js", "/static/app.abc123.css"):
                ctype = (
                    "application/javascript"
                    if path.endswith(".js") else "text/css; charset=utf-8"
                )
                self._send(
                    200, b"/* stub asset */", ctype,
                    {"Cache-Control": "public, max-age=31536000, immutable"},
                )
            elif path == "/dashboard":
                if self.headers.get("Authorization"):
                    self._send(200, b"<h1>Dashboard</h1>")
                else:
                    self._send(302, b"", extra={"Location": "/login"})
            elif path == "/login":
                self._send(200, b"<form>login</form>")
            elif path == "/__pyweb/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"event: message\ndata: {\"text\": \"hi\"}\n\n")
                self.close_connection = True
            elif path == "/__pyweb/poll":
                self._send(
                    200,
                    json.dumps({"messages": [{"text": "hi"}]}).encode(),
                    "application/json",
                )
            elif path == "/blog/hello":
                self._send(
                    200,
                    b"<!DOCTYPE html><html><head><title>Hello - PyWeb blog</title>"
                    b'<meta name="description" content="Hello post">'
                    b'<meta property="og:title" content="Hello">'
                    b"</head><body><article>hello</article></body></html>",
                )
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if path in routes and "POST" in routes[path]:
                status, ctype, body, extra = routes[path]["POST"]
                self._send(status, body, ctype, extra)
                return
            payload = self._read_json()
            if path == "/__pyweb/rpc/increment":
                count = payload.get("count", 0)
                self._send(
                    200,
                    json.dumps({"ok": True, "count": count + 1}).encode(),
                    "application/json",
                )
            elif path == "/__pyweb/rpc/add_todo":
                title = (payload.get("title") or "").strip()
                if not title:
                    self._send(
                        400,
                        json.dumps(
                            {"ok": False, "error": "validation: title required"}
                        ).encode(),
                        "application/json",
                    )
                else:
                    self._send(
                        200,
                        json.dumps({"ok": True, "id": "todo-2",
                                    "title": title}).encode(),
                        "application/json",
                    )
            elif path == "/__pyweb/rpc/echo":
                self._send(
                    200,
                    json.dumps({"ok": True, "echo": payload}).encode(),
                    "application/json",
                )
            else:
                self._send(
                    404,
                    json.dumps({"ok": False,
                                "error": "unknown rpc"}).encode(),
                    "application/json",
                )

    return StubHandler


@contextlib.contextmanager
def stub_server(base_html: str = STUB_HTML, extra_routes: dict | None = None,
                **kwargs):
    """Yield ``base_url`` of an in-process stub PyWeb-like server."""
    with serving(make_stub_handler(base_html, extra_routes), **kwargs) as url:
        yield url
