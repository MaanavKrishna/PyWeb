"""0.4.4: typed RPC arguments, request limits, slow clients, connection caps, security headers."""

import argparse
import dataclasses
import json
import socket
import time
from typing import Literal, Optional

import pytest

from pyweb import cli
from pyweb.compiler import compile_source
from pyweb.models import Email, IntegerField, Model, TextField
from pyweb.runtime.server import STREAM_SLOTS, Request, Server
from pyweb.serve import serve_in_thread

APP = "from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <p>x</p>\n"


def server(**kw):
    return Server(compile_source(APP), **kw)


def call(srv, name, args=None, headers=None, raw=None, client="10.0.0.1"):
    body = raw if raw is not None else json.dumps({"args": args or {}}).encode()
    res = srv.handle(Request("POST", f"/__pyweb/rpc/{name}", headers=headers or {}, body=body, client=client))
    return res.status, json.loads(res.body)


@dataclasses.dataclass
class Point:
    x: int
    y: int = 0


class Person(Model):
    name = TextField()
    age = IntegerField()


def test_arguments_are_checked_against_type_hints():
    srv = server()

    def total(ids: list[int], tags: Optional[dict[str, str]] = None, mode: Literal["a", "b"] = "a",
              where: Point = None, who: Person = None, mail: Email = None, name: str = "", n: int = 0) -> dict:
        return {"ids": ids, "tags": tags, "mode": mode, "x": where.x if where else None,
                "who": who.name if who else None, "name": name, "n": n}
    srv.register_rpc(total)
    ok = call(srv, "total", {"ids": ["1", 2], "tags": {"k": "v"}, "mode": "b", "where": {"x": 3},
                             "who": {"name": "Ada", "age": 36}, "mail": "a@b.c", "name": "z", "n": "5"})
    assert ok == (200, {"result": {"ids": [1, 2], "tags": {"k": "v"}, "mode": "b", "x": 3,
                                   "who": "Ada", "name": "z", "n": 5}})
    for bad, message in [
        ({"ids": "1,2"}, "total.ids expects a list"),
        ({"ids": [1, "x"]}, "total.ids[1] expects int"),
        ({"ids": [], "mode": "c"}, "total.mode must be one of"),
        ({"ids": [], "where": {"x": 1, "admin": True}}, "unknown field(s): admin"),
        ({"ids": [], "who": {"name": "A", "is_admin": 1}}, "unknown field(s): is_admin"),
        ({"ids": [], "mail": "nope"}, "valid email"),
        ({"ids": [], "name": {"$gt": ""}}, "total.name expects str"),
        ({"ids": [], "n": 1.5}, "total.n expects int"),
        ({"ids": [], "sneaky": 1}, "unknown argument(s): sneaky"),
        ({}, "missing required argument 'ids'"),
    ]:
        status, body = call(srv, "total", bad)
        assert status == 422 and message in body["error"]["message"], (bad, body)


def test_deeply_nested_json_is_refused_not_a_crash():
    srv = server()

    def f(x: list):
        return len(x)
    srv.register_rpc(f)
    status, body = call(srv, "f", raw=b'{"args": {"x": ' + b"[" * 200_000 + b"]" * 200_000 + b"}}")
    assert status == 422 and "too complex" in body["error"]["message"]
    assert call(srv, "f", {"x": [[1, [2]], "[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[["]})[0] == 200   # brackets in strings are fine


def test_cross_site_requests_are_refused():
    srv = server()

    def f():
        return 1
    srv.register_rpc(f)
    assert call(srv, "f", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert call(srv, "f", headers={"Sec-Fetch-Site": "same-origin"})[0] == 200


def test_open_live_connections_are_capped_per_address(monkeypatch):
    from pyweb import context as ctx
    from pyweb import realtime as rt
    monkeypatch.setenv("PYWEB_MAX_STREAMS_PER_CLIENT", "2")
    srv = server(auth_secret="k" * 32)
    rc = ctx.RequestContext(Request("GET", "/"), auth_secret="k" * 32)
    token = ctx.activate(rc)
    try:
        feed = rt.channel("room")
    finally:
        ctx.deactivate(token)
    get = lambda client: srv.handle(Request("GET", f"/__pyweb/events?feed={feed}", client=client))
    a, b = get("10.9.9.9"), get("10.9.9.9")
    assert a.status == b.status == 200
    assert get("10.9.9.9").status == 429                      # a third from the same address
    assert get("10.9.9.8").status == 200                      # others aren't affected
    a.body.close()
    assert get("10.9.9.9").status == 200                      # closing one frees a slot
    STREAM_SLOTS.open.clear()


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "app.pyweb").write_text(APP)
    cli.cmd_build(argparse.Namespace(file=str(tmp_path / "app.pyweb"), out=str(tmp_path / "dist"), budget=[]))
    return str(tmp_path / "dist")


def raw_request(port, data, wait=0.0):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(data)
    if wait:
        time.sleep(wait)
    out = b""
    try:
        while chunk := s.recv(65536):
            out += chunk
    except (TimeoutError, ConnectionResetError):
        pass
    s.close()
    return out


def test_huge_headers_get_431(dist):
    httpd, _ = serve_in_thread(dist, rate_limit=False)
    try:
        port = httpd.server_address[1]
        req = b"GET /healthz HTTP/1.1\r\nHost: x\r\n" + b"".join(
            b"X-Pad-%d: %s\r\n" % (i, b"a" * 900) for i in range(40)) + b"Connection: close\r\n\r\n"
        assert b" 431 " in raw_request(port, req)[:40]
        ok = raw_request(port, b"GET /healthz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        assert b" 200 " in ok[:20]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_slow_clients_are_disconnected(dist, monkeypatch):
    from pyweb import serve
    monkeypatch.setattr(serve.LimitedHandler, "timeout", 1)
    httpd, _ = serve_in_thread(dist, rate_limit=False)
    try:
        start = time.monotonic()
        s = socket.create_connection(("127.0.0.1", httpd.server_address[1]), timeout=10)
        s.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n")      # ...and never finishes the request
        data = s.recv(100)
        s.close()
        assert data in (b"",) or b"408" in data or b"400" in data
        assert time.monotonic() - start < 5
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_connection_cap_answers_503(dist, monkeypatch):
    monkeypatch.setenv("PYWEB_MAX_CONNECTIONS", "1")
    httpd, _ = serve_in_thread(dist, rate_limit=False)
    try:
        port = httpd.server_address[1]
        hog = socket.create_connection(("127.0.0.1", port), timeout=5)
        hog.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n")    # holds the only slot
        time.sleep(0.2)
        assert b"503" in raw_request(port, b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n")[:30]
        hog.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_security_headers(dist, monkeypatch):
    import urllib.request as u
    from pyweb.hosting import Site
    for secure in ("", "1"):
        monkeypatch.setenv("PYWEB_COOKIE_SECURE", secure)
        httpd, _ = serve_in_thread(dist, rate_limit=False)
        try:
            with u.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/") as res:
                h = res.headers
        finally:
            httpd.shutdown()
            httpd.server_close()
        _, site_h, _ = Site(dist, secure_cookies=bool(secure)).respond("GET", "/", {})
        for headers in (h, dict(site_h)):
            assert headers["Permissions-Policy"].startswith("camera=()")
            assert headers["Cross-Origin-Opener-Policy"] == "same-origin-allow-popups"
            assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
            assert ("Strict-Transport-Security" in headers) is bool(secure)
