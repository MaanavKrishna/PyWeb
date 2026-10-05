"""Blocker fixes: serve/health, migrations, importmap, cache TTL, CSRF."""

import json
import os
import subprocess
import sys
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


def test_error_page_escapes_title_and_message():
    from pyweb.runtime.server import Server
    s = Server({"pages": {}, "rpc": []})
    r = s.error_page(500, title="<script>alert(1)</script>",
                     message="<img src=x onerror=y>")
    assert "<script>" not in r.body and "<img" not in r.body
    assert "&lt;script&gt;" in r.body


def test_parse_cookies_and_malformed_input():
    assert _serve.parse_cookies("a=1; b=two; pyweb_session=tok123") == {
        "a": "1", "b": "two", "pyweb_session": "tok123"}
    assert _serve.parse_cookies("") == {}
    assert _serve.parse_cookies(None) == {}
    assert _serve.parse_cookies(";;;noequal;;;x=1") == {"x": "1"}


def test_cookie_session_reaches_rpc_over_http(tmp_path):
    """End-to-end: Cookie header over real HTTP must authenticate RPC."""
    import sys
    from pyweb import auth as _auth
    secret = "test-secret-for-cookie-e2e"
    dist = _dist(str(tmp_path))
    factory_src = (
        "from pyweb.runtime.server import Server\n"
        f"SECRET = {secret!r}\n"
        "def whoami():\n"
        "    return 'ok-u1'\n"
        "whoami.__pyweb_auth__ = True\n"
        "def factory(manifest):\n"
        "    server = Server({'pages': {}, 'rpc': []}, auth_secret=SECRET)\n"
        "    server.register_rpc(whoami)\n"
        "    return None, {'whoami': whoami}\n"
    )
    with open(os.path.join(str(tmp_path), "cookieapp.py"), "w") as fh:
        fh.write(factory_src)
    sys.path.insert(0, str(tmp_path))
    try:
        httpd, _thread = _serve.serve_in_thread(
            dist, app_factory="cookieapp:factory", rate_limit=False,
            auth_secret=secret)
    finally:
        sys.path.remove(str(tmp_path))
    try:
        import urllib.request
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        token = _auth.issue_session({"sub": "u1"}, secret)

        def post(cookie=None):
            req = urllib.request.Request(
                base + "/__pyweb/rpc/whoami", data=b'{"args": {}}',
                headers={"Content-Type": "application/json"})
            if cookie:
                req.add_header("Cookie", cookie)
            try:
                return urllib.request.urlopen(req).status
            except urllib.error.HTTPError as exc:
                return exc.code

        assert post() == 401  # no session -> gated
        assert post(f"pyweb_session={token}; other=1") == 200  # cookie -> in
    finally:
        httpd.shutdown()

def test_body_cap_and_head_and_security_headers(tmp_path):
    from pyweb.runtime.server import Request, Server
    dist = _dist(str(tmp_path))
    # Runtime-level cap (direct Server use, no serve.py).
    server = Server({"pages": {}, "rpc": []}, max_body=16)

    def tiny():
        return "x"

    server.register_rpc(tiny)
    big = Request("POST", "/__pyweb/rpc/tiny", {},
                  b'{"args": {"k": "' + b"y" * 64 + b'"}}')
    resp = server.handle(big)
    assert resp.status == 413, resp.body
    assert json.loads(resp.body)["error"]["code"] == "http_413"

    import urllib.request
    httpd, _thread = _serve.serve_in_thread(dist, rate_limit=False,
                                            max_body=32)
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        def raw(method, path, data=None, headers=None):
            req = urllib.request.Request(base + path, data=data,
                                         headers=headers or {},
                                         method=method)
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, dict(r.headers), r.read()
            except urllib.error.HTTPError as exc:
                return exc.code, dict(exc.headers), exc.read()

        status, headers, body = raw("GET", "/healthz")
        assert status == 200
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("Referrer-Policy") == "same-origin"
        assert headers.get("X-Frame-Options") == "SAMEORIGIN"
        # HEAD mirrors GET headers, no body.
        h_status, h_headers, h_body = raw("HEAD", "/healthz")
        assert h_status == 200 and h_body == b""
        # A real length (the uptime in the body can differ by a digit between the two calls).
        assert int(h_headers.get("Content-Length")) > 0
        # Oversize POST -> 413 at the HTTP layer (before RPC dispatch).
        big_status, _, big_raw = raw(
            "POST", "/__pyweb/rpc/anything", data=b"x" * 64,
            headers={"Content-Type": "application/json"})
        assert big_status == 413
        assert b"body-too-large" in big_raw
        # 2 MiB oversize (multi-chunk drain): client must receive the 413
        # instead of a broken pipe, and the connection must stay usable.
        huge_status, _, huge_raw = raw(
            "POST", "/__pyweb/rpc/anything", data=b"x" * (2 * 1024 * 1024),
            headers={"Content-Type": "application/json"})
        assert huge_status == 413
        assert b"body-too-large" in huge_raw
        again, _, _ = raw("GET", "/healthz")
        assert again == 200
    finally:
        httpd.shutdown()



def test_version_single_source_of_truth():
    import re
    import pyweb
    from pyweb import serve as _serve_mod
    m = re.search(r'^version\s*=\s*"([^"]+)"',
                  open("pyproject.toml").read(), re.M)
    assert m, "pyproject must declare version"
    assert pyweb.__version__ == m.group(1)
    assert _serve_mod.VERSION == m.group(1)

def test_dev_server_traversal_blocked_over_http(tmp_path, monkeypatch):
    """Boot the real `pyweb dev` server; .. escapes must not leak files."""
    import threading
    import time
    import urllib.request
    import urllib.error
    from pyweb.cli import cmd_dev

    app_src = ('from pyweb import App\napp = App()\n@app.page("/")\n'
               "def Home():\n    <main>\n        <h1>Hi</h1>\n    </main>\n")
    app_file = os.path.join(str(tmp_path), "app.pyweb")
    with open(app_file, "w") as fh:
        fh.write(app_src)
    secret = os.path.join(str(tmp_path), "secret.txt")
    with open(secret, "w") as fh:
        fh.write("top-secret-dev")
    monkeypatch.chdir(tmp_path)

    class Args:
        file = app_file
        port = 0  # ephemeral; cmd_dev binds ("127.0.0.1", port)
        no_reload = True

    # cmd_dev binds a fixed port arg; patch HTTPServer choice via port scan:
    # instead run with port=0 unsupported -> find a free port ourselves.
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        Args.port = sock.getsockname()[1]
    thread = threading.Thread(target=cmd_dev, args=(Args(),), daemon=True)
    thread.start()
    for _ in range(100):
        time.sleep(0.05)
        try:
            with socket.create_connection(("127.0.0.1", Args.port),
                                          timeout=0.2):
                break
        except OSError:
            continue

    def get(path):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{Args.port}{path}") as r:
                return r.status, r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except OSError:
            return None, b""

    try:
        status, body = get("/")
        assert status == 200 and b"Hi" in body
        status, body = get("/static/../../secret.txt")
        assert status in (403, 404) and b"top-secret-dev" not in body
    finally:
        pass  # daemon thread dies with the test process

def test_graceful_shutdown_drains_and_handles_sigterm(tmp_path):
    """SIGTERM handler shuts the server down instead of dying mid-request."""
    import signal
    dist = _dist(str(tmp_path))
    httpd, _thread = _serve.serve_in_thread(dist, rate_limit=False)
    prev = _serve.install_shutdown_handlers(httpd, timeout=5.0)
    try:
        assert signal.getsignal(signal.SIGTERM) != prev.get(signal.SIGTERM)
        import urllib.request
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        # Simulate delivery: invoke the installed handler directly.
        handler = signal.getsignal(signal.SIGTERM)
        handler(signal.SIGTERM, None)
        _thread.join(timeout=10)
        assert not _thread.is_alive()  # drained: serve_forever returned
        try:
            urllib.request.urlopen(base + "/healthz", timeout=2)
            listening = True
        except Exception:
            listening = False
        assert not listening
    finally:
        for sig, old in prev.items():
            try:
                signal.signal(sig, old)
            except (ValueError, OSError):
                pass
        httpd.server_close()

def test_db_rollback_cli_steps_and_to(tmp_path, capsys):
    """`pyweb db rollback` reverts via down.sql; --to keeps the target."""
    import sqlite3
    from pyweb.cli import main as cli_main
    from pyweb.db import migrate as _mig

    migdir = os.path.join(str(tmp_path), "migrations")
    os.makedirs(migdir)
    db_path = os.path.join(str(tmp_path), "t.db")

    def mig(ver, up, down):
        with open(os.path.join(migdir, f"{ver}_m.up.sql"), "w") as fh:
            fh.write(up)
        with open(os.path.join(migdir, f"{ver}_m.down.sql"), "w") as fh:
            fh.write(down)

    mig("001", "CREATE TABLE t1 (id INTEGER);",
        "DROP TABLE t1;")
    mig("002", "CREATE TABLE t2 (id INTEGER);",
        "DROP TABLE t2;")
    applied = _mig.migrate(db_path, migdir)
    assert applied == ["001_m", "002_m"]

    cli_main(["db", "rollback", "--database", db_path,
              "--migrations", migdir, "--steps", "1"])
    out = capsys.readouterr().out
    assert "002_m" in out
    con = sqlite3.connect(db_path)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "t2" not in tables and "t1" in tables

    cli_main(["db", "rollback", "--database", db_path,
              "--migrations", migdir, "--to", "001_m"])
    con = sqlite3.connect(db_path)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "t1" in tables  # target kept; nothing after it
    with pytest.raises(SystemExit, match="not an applied migration"):
        cli_main(["db", "rollback", "--database", db_path,
                  "--migrations", migdir, "--to", "999_nope"])




def test_load_factory_errors():
    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="expected module:attr"):
        _serve.load_factory("justamodule")
    with _pytest.raises(RuntimeError, match="cannot import"):
        _serve.load_factory("no_such_mod_xyz:factory")



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
    assert proc.returncode == 0 and proc.stdout.strip() == __import__("re").search(r'^version = "([^"]+)"', open("pyproject.toml").read(), __import__("re").M).group(1)



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
