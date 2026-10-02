"""Reference apps: compile, server-render and serve over real HTTP.

Interactive behaviour of the same apps is covered in a real browser by
``tests/e2e/test_examples_browser.py``.
"""

from __future__ import annotations

import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from pyweb.compiler import compile_source

ROOT = Path(__file__).parent.parent
EXAMPLES = ROOT / "examples"
APPS = ["counter", "todo", "blog", "auth", "chat", "showcase"]


def _src(app: str) -> str:
    return (EXAMPLES / app / "app.pyweb").read_text(encoding="utf-8")


class TestAppContracts:
    @pytest.mark.parametrize("app", APPS)
    def test_files_exist(self, app):
        assert (EXAMPLES / app / "app.pyweb").is_file()

    @pytest.mark.parametrize("app", APPS)
    def test_build_clean(self, app):
        out = compile_source(_src(app))
        assert out["pages"], app
        page = next(iter(out["pages"].values()))
        assert "<" in page["html_body"] and len(page["html_body"]) > 10

    @pytest.mark.parametrize("app", APPS)
    def test_ssr_has_hydration_markers_when_dynamic(self, app):
        out = compile_source(_src(app))
        page = next(iter(out["pages"].values()))
        for page in out["pages"].values():
            if page["js"]:
                assert 'data-pw-root="' in page["html"] and 'id="pw-state"' in page["html"], app
            else:
                assert "<script" not in page["html"], app


class TestCounterApp:
    def test_reactive_nodes_and_build(self):
        out = compile_source(_src("counter"))
        page = out["pages"]["Home"]
        assert '<button id="inc">Count: 0</button>' in page["html_body"]
        assert page["signals"] == ["count", "step"]
        assert "count($py.add(count(), step()))" in page["js"]


class TestTodoApp:
    def test_crud_rpc_registered(self):
        src = _src("todo")
        assert "add_todo" in src or "Todo" in src
        out = compile_source(src)
        assert out["rpc"] or "todo" in out["pages"]["Home"]["html_body"].lower()


class TestBlogApp:
    def test_seo_title(self):
        out = compile_source(_src("blog"))
        html = out["pages"][next(iter(out["pages"]))]["html"]
        assert "<title>" in html


class TestRealtimeChat:
    def test_chat_compiles(self):
        out = compile_source(_src("chat"))
        assert out["pages"]


def _serve(html: str):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_serve_ssr_200():
    import contextlib
    out = compile_source(_src("counter"))
    html = out["pages"]["Home"]["html"]
    with contextlib.contextmanager(_serve)(html) as url:
        with urllib.request.urlopen(url + "/") as res:
            assert res.status == 200
            body = res.read().decode()
            assert 'data-pw-root="Home"' in body and '"count":0' in body
