"""Tests for the dev server: watcher, rebuild, SSE, overlay, sourcemaps (Track D)."""
from __future__ import annotations

import json
import urllib.request

from pyweb.dev import (
    DevOptions,
    DevServer,
    classify_change,
    inject_reload_snippet,
    map_stack,
    snapshot,
)


def test_snapshot_and_rebuild(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    server = DevServer(DevOptions(root=tmp_path, port=0))
    snap = snapshot(tmp_path)
    assert "app.py" in snap
    (tmp_path / "app.py").write_text("x = 2\n", encoding="utf-8")
    _, changed = server.watch_once(snap)
    assert changed == ["app.py"]
    server.rebuild(changed)
    assert server.build_count == 1
    assert json.loads((tmp_path / ".pyweb" / "last-build.json").read_text())["changed"] == ["app.py"]


def test_classify_signal_only_vs_full(tmp_path):
    hot = tmp_path / "hot.py"
    hot.write_text("count.set(1)\n", encoding="utf-8")
    assert classify_change(["hot.py"], tmp_path) == "hot-signal"
    (tmp_path / "other.js").write_text("x\n", encoding="utf-8")
    assert classify_change(["other.js"], tmp_path) == "reload"
    assert classify_change([], tmp_path) == "reload"


def test_map_stack_degrades_without_artifacts(tmp_path):
    res = map_stack("Error: boom\n    at foo (app.js:1:2)", tmp_path)
    assert res["mapped"] is False and res["frames"][0].startswith("Error: boom")


def test_map_stack_uses_artifacts(tmp_path):
    (tmp_path / ".pyweb").mkdir()
    (tmp_path / ".pyweb" / "a.map.json").write_text(
        json.dumps({"mappings": {"app.js:1:2": ".pyweb:41"}}), encoding="utf-8")
    res = map_stack("Error: boom\n    at foo (app.js:1:2)", tmp_path)
    assert res["mapped"] is True and ".pyweb:41" in res["frames"][1]


def test_status_lines_cover_compiler_server_db_debugger(tmp_path):
    server = DevServer(DevOptions(root=tmp_path, port=1234))
    lines = server.status_lines(["a.py"], "reload")
    joined = "\n".join(lines)
    assert all(k in joined for k in ("compiler:", "server:", "db:", "debugger:"))


def test_http_overlay_sourcemap_and_injection(tmp_path):
    (tmp_path / "index.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
    (tmp_path / "app.py").write_text("x=1\n", encoding="utf-8")
    server = DevServer(DevOptions(root=tmp_path, port=0))
    server.serve_forever(block=False)
    try:
        base = f"http://127.0.0.1:{server.options.port}"
        with urllib.request.urlopen(base + "/__pyweb__/overlay.js") as r:
            assert "__pyweb_error_overlay__" in r.read().decode()
        with urllib.request.urlopen(base + "/__pyweb__/sourcemap?stack=boom") as r:
            assert json.loads(r.read())["mapped"] is False
        with urllib.request.urlopen(base + "/index.html") as r:
            assert "data-pyweb-reload" in r.read().decode()
        with urllib.request.urlopen(base + "/__pyweb__/status") as r:
            assert json.loads(r.read())["build"] == 0
    finally:
        server.stop()


def test_inject_reload_snippet_without_body():
    assert b"data-pyweb-reload" in inject_reload_snippet(b"<html>hi")
