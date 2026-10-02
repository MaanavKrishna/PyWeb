"""End-to-end without a browser: TestClient (SSR + RPC + sessions), build, serve."""

import json
import subprocess
import sys
import urllib.request

import pytest

from pyweb.compiler import compile_source
from pyweb.rpc import RPCError
from pyweb.runtime.server import Request, Server
from pyweb.testing import TestClient

NOTES = """from pyweb import App, RPCError, redirect, server, session

app = App(title="Notes")
NOTES = {}


@server
def sign_in(name: str) -> bool:
    session.login(name)
    return True


@server
def add(text: str) -> list:
    user = session.require()
    if not text.strip():
        raise RPCError("validation_error", "empty note")
    NOTES.setdefault(user["sub"], []).append(text)
    return NOTES[user["sub"]]


@app.page("/")
def Home():
    user = session.user()
    if not user:
        return redirect("/login")
    who = user["sub"]
    notes = NOTES.get(who, [])
    draft = ""

    def save():
        notes = add(draft)

    <main>
        <h1>{who}</h1>
        <input bind={draft} />
        <button onclick={save}>Save</button>
        for n in notes:
            <p>{n}</p>
    </main>


@app.page("/login")
def Login():
    <p>please sign in</p>


@app.page("/users/{user_id}")
def User(user_id: int):
    label = "user #" + str(user_id)
    <h1>{label}</h1>
"""


def test_full_stack_session_rpc_and_ssr():
    client = TestClient(source=NOTES)
    r = client.get("/")
    assert r.status == 303 and r.header("Location") == "/login"
    with pytest.raises(RPCError) as e:
        client.rpc("add", text="x")
    assert e.value.code == "unauthenticated"
    assert client.rpc("sign_in", name="ada") is True
    assert "pyweb_session" in client.cookies
    assert client.rpc("add", text="first") == ["first"]
    with pytest.raises(RPCError) as e:
        client.rpc("add", text="  ")
    assert e.value.code == "validation_error" and str(e.value) == "empty note"
    page = client.get("/")
    assert page.status == 200
    assert "<h1>ada</h1>" in page.text and "<p>first</p>" in page.text
    assert '"notes":["first"]' in page.text and '"user"' not in page.text


def test_rpc_validates_argument_types():
    client = TestClient(source=NOTES)
    with pytest.raises(RPCError) as e:
        client.rpc("sign_in")
    assert e.value.code == "validation_error"
    resp = client.request("GET", "/__pyweb/rpc/sign_in")
    assert resp.status == 405


def test_route_params_are_typed():
    client = TestClient(source=NOTES)
    assert "<h1>user #42</h1>" in client.get("/users/42").text
    assert client.get("/users/abc").status == 404
    assert client.get("/nope").status == 404


def test_page_errors_are_500_with_request_id():
    src = "from pyweb import App\napp = App()\n@app.page('/')\ndef H():\n    x = 1 // 0\n    <p>{x}</p>\n"
    client = TestClient(source=src)
    r = client.get("/")
    assert r.status == 500 and "ZeroDivisionError" in r.text


def test_example_apps_render_server_side():
    client = TestClient("examples/todo/app.pyweb")
    page = client.get("/")
    assert page.status == 200 and "Nothing to do yet." in page.text
    assert client.get("/static/app.css").status == 200


def test_build_artifacts(tmp_path):
    r = subprocess.run([sys.executable, "-m", "pyweb.cli", "build",
                        "examples/counter/app.pyweb", "--out", str(tmp_path)],
                       capture_output=True, text=True, cwd=".")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "manifest.json").exists()
    assert (tmp_path / "static" / "Home.js").exists()
    assert (tmp_path / "static" / "Home.js.map").exists()
    assert (tmp_path / "app.pyweb").exists() and (tmp_path / "Dockerfile").exists()
    man = json.loads((tmp_path / "manifest.json").read_text())
    assert man["pages"]["Home"]["signals"] == ["count", "step"]


def test_built_dist_serves_pages_and_rpc(tmp_path):
    src = tmp_path / "app.pyweb"
    src.write_text(NOTES)
    dist = tmp_path / "dist"
    r = subprocess.run([sys.executable, "-m", "pyweb.cli", "build", str(src), "--out", str(dist),
                        "--production"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    from pyweb.serve import serve_in_thread
    httpd, _t = serve_in_thread(str(dist), rate_limit=False)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        req = urllib.request.Request(base + "/__pyweb/rpc/sign_in", data=b'{"args": {"name": "bo"}}',
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as res:
            cookie = res.headers["Set-Cookie"].split(";")[0]
            assert json.loads(res.read()) == {"result": True}
        page = urllib.request.urlopen(urllib.request.Request(base + "/", headers={"Cookie": cookie}))
        html = page.read().decode()
        assert "<h1>bo</h1>" in html
        assert "Content-Security-Policy" in page.headers
        man = json.loads((dist / "manifest.json").read_text())
        assert f'/static/{man["pages"]["Home"]["js"]}' in html
    finally:
        httpd.shutdown()
        httpd.server_close()


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
