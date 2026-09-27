"""Blocker fixes: serve/health, migrations, importmap, cache TTL, CSRF."""

import json
import os
import subprocess
import sys
import tempfile
import urllib.request

import pytest

from pyweb import build as _build
from pyweb import serve as _serve
from pyweb.compiler import compile_source
from pyweb.db import migrate as _migrate


def _dist(tmp, source=None):
    src = source or ('from pyweb import App\napp = App()\n@app.page("/")\n'
                     "def Home():\n    count = 0\n"
                     "    def increment():\n        count += 1\n"
                     "    <main>\n        <h1>Hi</h1>\n"
                     "        <button onclick={increment}>\n"
                     "            Count: {count}\n        </button>\n    </main>\n")
    out = compile_source(src, filename="app.pyweb")
    dist = os.path.join(tmp, "dist")
    _build.build(out, dist)
    return dist


def test_healthz_and_threaded_static(tmp_path):
    dist = _dist(str(tmp_path))
    httpd, thread = _serve.serve_in_thread(dist)
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with urllib.request.urlopen(base + "/healthz", timeout=5) as r:
            body = json.load(r)
        assert body["ok"] and body["version"] == _serve.VERSION
        with open(os.path.join(dist, "manifest.json")) as fh:
            rt = json.load(fh)["runtime"]
        with urllib.request.urlopen(base + f"/static/{rt}", timeout=5) as r:
            assert r.status == 200 and "Cache-Control" in r.headers
        # Path traversal contained.
        try:
            urllib.request.urlopen(base + "/static/../manifest.json", timeout=5)
            assert False, "traversal served"
        except Exception as exc:
            assert "403" in str(exc) or "404" in str(exc)
    finally:
        httpd.shutdown()


def test_serve_without_app_rpc_501(tmp_path):
    dist = _dist(str(tmp_path))
    httpd, thread = _serve.serve_in_thread(dist)
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        req = urllib.request.Request(base + "/__pyweb/rpc/x", data=b"{}",
                                     method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "rpc should be unavailable"
        except Exception as exc:
            assert "501" in str(exc)
    finally:
        httpd.shutdown()

def test_serve_app_factory_live_rpc(tmp_path):
    """Item 2: ``--app mod:attr`` mounts real RPC impls end to end."""
    import sys
    import textwrap
    src = ('from pyweb import App\napp = App()\n@app.page("/")\n'
           "def Home():\n    n = 0\n"
           "    <main>\n        <h1>{n}</h1>\n    </main>\n"
           "@server\ndef echo(msg: str) -> str:\n    return 'hi:' + msg\n")
    out = compile_source(src, filename="app.pyweb")
    dist = os.path.join(str(tmp_path), "dist")
    _build.build(out, dist)
    fac = os.path.join(str(tmp_path), "factory_mod.py")
    with open(fac, "w") as fh:
        fh.write(textwrap.dedent("""\
            def echo(msg: str) -> str:
                return 'hi:' + msg
            def app(manifest):
                compiled = {'pages': {}, 'rpc': [{'name': 'echo'}]}
                return compiled, {'echo': echo}
            """))
    sys.path.insert(0, str(tmp_path))
    try:
        httpd, thread = _serve.serve_in_thread(
            dist, app_factory="factory_mod:app", rate_limit=False)
        try:
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            payload = json.dumps({"args": {"msg": "you"}}).encode()
            req = urllib.request.Request(
                base + "/__pyweb/rpc/echo", data=payload, method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                body = json.load(r)
            assert body["result"] == "hi:you", body
            assert r.headers.get("X-Request-Id"), "trace id missing"
        finally:
            httpd.shutdown()
    finally:
        sys.path.remove(str(tmp_path))


def test_serve_rate_limit_default_on(tmp_path):
    """Item 3: serve() protects RPC by default."""
    import sys
    import textwrap
    src = ('from pyweb import App\napp = App()\n@app.page("/")\n'
           "def Home():\n    n = 0\n"
           "    <main>\n        <h1>{n}</h1>\n    </main>\n")
    out = compile_source(src, filename="app.pyweb")
    dist = os.path.join(str(tmp_path), "dist")
    _build.build(out, dist)
    fac = os.path.join(str(tmp_path), "rl_mod.py")
    with open(fac, "w") as fh:
        fh.write(textwrap.dedent("""\
            def ping() -> str:
                return 'pong'
            def app(manifest):
                return {'pages': {}, 'rpc': [{'name': 'ping'}]}, {'ping': ping}
            """))
    sys.path.insert(0, str(tmp_path))
    try:
        from pyweb import rpc as _rpc
        httpd, thread = _serve.serve_in_thread(
            dist, app_factory="rl_mod:app",
            rate_limit=_rpc.RateLimiter(max_calls=2, window=60.0))
        try:
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            codes = []
            for _ in range(4):
                req = urllib.request.Request(
                    base + "/__pyweb/rpc/ping", data=b"{}", method="POST",
                    headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=5) as r:
                        codes.append(r.status)
                except Exception as exc:
                    codes.append(429 if "429" in str(exc) else -1)
            assert codes[:2] == [200, 200] and 429 in codes[2:], codes
        finally:
            httpd.shutdown()
    finally:
        sys.path.remove(str(tmp_path))


def test_error_pages_default_and_override(tmp_path):
    """Item 8: branded 404 with path escape + app override shells."""
    from pyweb.runtime.server import Request, Server
    srv = Server({"pages": {}, "rpc": []})
    resp = srv.handle(Request("GET", "/nope<script>", {}, b""))
    assert resp.status == 404
    assert "/nope&lt;script&gt;" in resp.body  # escaped, not reflected
    assert resp.headers["Content-Type"] == "text/html"
    srv.register_error(404, "<h1>gone: {{path}} ({{request_id}})</h1>")
    resp2 = srv.handle(Request("GET", "/away", {}, b""))
    assert "<h1>gone: /away (" in resp2.body


def test_secure_cookie_flag():
    """Item 9: Secure opt-in for production HTTPS."""
    from pyweb import auth as _auth
    plain = _auth.login_response("u1", "s3cret")
    assert "Secure" not in plain.headers["Set-Cookie"]
    prod = _auth.login_response("u1", "s3cret", secure=True)
    cookie = prod.headers["Set-Cookie"]
    assert "; Secure" in cookie and "HttpOnly" in cookie
    out = _auth.logout_response(secure=True)
    assert "; Secure" in out.headers["Set-Cookie"]


def test_rpc_trace_id_propagation():
    """Item 10: incoming traceparent flows to X-Request-Id + logs."""
    from pyweb.runtime.server import Request, Server
    srv = Server({"pages": {}, "rpc": []})
    tp = ("00-4bf92f3577b34da6a3ce929d0e0e4736-"
          "00f067aa0ba902b7-01")
    req = Request("POST", "/__pyweb/rpc/missing", {"traceparent": tp}, b"{}")
    resp = srv.handle_rpc(req)
    assert resp.headers.get("X-Request-Id") == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert resp.headers.get("traceparent", "").startswith("00-4bf92f3577b34da")


def test_cli_version():
    """Item 7: pyweb --version prints the package version."""
    proc = subprocess.run(
        [sys.executable, "-m", "pyweb.cli", "--version"],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and proc.stdout.strip().startswith("1.")



def test_migrations_idempotent_and_rollback(tmp_path):
    d = str(tmp_path / "migrations")
    _migrate.new_migration(d, "create todos")
    with open(os.path.join(d, "001_create_todos.up.sql"), "a") as fh:
        fh.write("CREATE TABLE todos (id TEXT PRIMARY KEY, title TEXT);")
    with open(os.path.join(d, "001_create_todos.down.sql"), "a") as fh:
        fh.write("DROP TABLE todos;")
    db = "sqlite:///" + str(tmp_path / "t.db")
    assert _migrate.migrate(db, d) == ["001_create_todos"]
    assert _migrate.migrate(db, d) == []  # idempotent
    assert _migrate.status(db, d) == [("001_create_todos", True)]
    assert _migrate.rollback(db, d) == ["001_create_todos"]
    assert _migrate.status(db, d) == [("001_create_todos", False)]


def test_migrations_reject_orphan_down(tmp_path):
    d = str(tmp_path / "m")
    os.makedirs(d)
    with open(os.path.join(d, "001_lonely.up.sql"), "w") as fh:
        fh.write("SELECT 1;")
    with pytest.raises(ValueError):
        _migrate.migrate(":memory:", d)


def test_npm_importmap_in_build(tmp_path):
    src = ('from pyweb import App\nfrom npm.chartjs import Chart\n'
           'app = App()\n@app.page("/")\n'
           "def Home():\n    chart = package('chart.js@4.4.0')\n"
           "    title = 'Hi'\n"
           "    <main>\n        <h1>{title}</h1>\n    </main>\n")
    dist = _dist(str(tmp_path), src)
    with open(os.path.join(dist, "manifest.json")) as fh:
        manifest = json.load(fh)
    assert manifest["npm"]["chart.js"] == "4.4.0"
    with open(os.path.join(dist, "server", "Home.html")) as fh:
        shell = fh.read()
    assert "importmap" in shell and "esm.sh" in shell


def test_cache_minutes_means_minutes():
    from pyweb.cache import MemoryCache
    now = [1000.0]
    c = MemoryCache(time_fn=lambda: now[0])
    c.set("k", "v", minutes=5)
    now[0] += 5 * 60 - 1
    assert c.get_value("k") == "v"
    now[0] += 2
    assert c.get_value("k") is None


def test_render_form_csrf():
    from pyweb import forms

    class M:
        _fields = {"email": str}
    html = forms.render_form(M, csrf_token="tok<123>")
    assert 'name="csrf_token"' in html and "tok&lt;123&gt;" in html
    assert 'name="csrf_token"' not in forms.render_form(M)


def test_cli_commands_exist():
    for cmd in ("serve", "db", "test", "fmt", "lint"):
        proc = subprocess.run(
            [sys.executable, "-m", "pyweb.cli", cmd, "--help"],
            capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, (cmd, proc.stderr)
