"""End-to-end: SSR + RPC + virtual client, build artifacts, full apps."""

import json
import subprocess
import sys
from pathlib import Path

from pyweb import testing
from pyweb.compiler import compile_source
from pyweb.runtime.server import Request, Server


BLOG = """from pyweb import App, server
from pyweb import Model

app = App()

class Post(Model):
    title: str

@server
def all_posts() -> str:
    return 'hello'

@app.page("/")
def Home():
    title = ""
    def publish():
        title = "x"
    <main>
        <h1>Blog</h1>
        <input bind={title} />
        <button onclick={publish}>Publish</button>
    </main>
"""


def test_full_stack_blog():
    out = compile_source(BLOG)

    def all_posts() -> str:
        return "hello"

    client = testing.Client(out, {"all_posts": all_posts})
    page = client.open("/")
    assert page.text("Blog")
    assert client.rpc("all_posts") == "hello"
    page.click("Publish").fill("title", "hi")
    assert client.events[0][0] == "click"


def test_checkout_flow_like_spec():
    src = Path("examples/todo/app.pyweb").read_text()
    out = compile_source(src)
    client = testing.Client(out)
    page = client.open("/")
    assert page.text("Todos")


def test_build_artifacts(tmp_path):
    r = subprocess.run([sys.executable, "-m", "pyweb.cli", "build",
                        "examples/counter/app.pyweb", "--out", str(tmp_path)],
                       capture_output=True, text=True, cwd=".")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "manifest.json").exists()
    assert (tmp_path / "static" / "Home.js").exists()
    assert (tmp_path / "static" / "Home.js.map").exists()
    man = json.loads((tmp_path / "manifest.json").read_text())
    assert man["pages"]["Home"]["signals"] == ["count"]


def test_check_command_ok_and_secret_fail(tmp_path):
    ok = subprocess.run([sys.executable, "-m", "pyweb.cli", "check", "examples/counter/app.pyweb"],
                        capture_output=True, text=True)
    assert ok.returncode == 0 and "ok:" in ok.stdout
    bad = tmp_path / "bad.pyweb"
    bad.write_text("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    API_KEY = 'x'\n    <p>{API_KEY}</p>\n")
    r = subprocess.run([sys.executable, "-m", "pyweb.cli", "check", str(bad)],
                       capture_output=True, text=True)
    assert r.returncode == 1


def test_static_bundle_tiny():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <h1>Hi</h1>\n")
    assert len(out["pages"]["H"]["js"].encode()) < 1500
    assert len(out["pages"]["H"]["html"].encode()) < 1500


def test_request_ids_unique():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <p>x</p>\n")
    s = Server(out)
    a = s.handle(Request("GET", "/"))
    b = s.handle(Request("GET", "/"))
    assert a.headers["X-Request-Id"] != b.headers["X-Request-Id"]


def test_dev_server_serves(tmp_path):
    import socket
    import threading
    import time
    import urllib.request
    from pyweb.cli import cmd_dev

    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()

    class A:
        file = "examples/counter/app.pyweb"
    A.port = port
    t = threading.Thread(target=cmd_dev, args=(A(),), daemon=True)
    t.start()
    body = js = None
    for _ in range(100):
        try:
            body = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).read().decode()
            js = urllib.request.urlopen(f"http://127.0.0.1:{port}/static/Home.js", timeout=1).read().decode()
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.05)
    assert body is not None and "Counter" in body
    assert js is not None and "count" in js
