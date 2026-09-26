"""Track B tests: server runtime + database (acceptance criteria)."""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pyweb.app import PyWeb  # noqa: E402
from pyweb.cache import MemoryCache, RedisCache, get_cache  # noqa: E402
from pyweb.db import (  # noqa: E402
    PostgresDB,
    Query,
    SQLiteDB,
    apply,
    autogen,
)
from pyweb.models import IntegerField, Model, TextField  # noqa: E402
from pyweb.runtime.server import (  # noqa: E402
    HEALTH_PATH,
    Request,
    Response,
    Server,
    StaticFilesMiddleware,
    new_csrf_token,
    sign_cookie,
    unsign_cookie,
)


class Item(Model):
    __table__ = "items"
    id = IntegerField(primary_key=True)
    name = TextField(nullable=False)
    qty = IntegerField(default=0)


@pytest.fixture()
def db():
    d = SQLiteDB(":memory:")
    d.execute(Item.schema_sql())
    Item.bind(d)
    yield d
    Item._db = None
    d.close()


# ---------------------------------------------------------------- models/CRUD


def test_sqlite_crud_roundtrip(db):
    item = Item.create(name="apple", qty=3)
    assert item.id is not None
    got = Item.get(item.id)
    assert got.name == "apple" and got.qty == 3
    got.qty = 9
    got.save()
    assert Item.get(item.id).qty == 9
    assert len(Item.all()) == 1
    assert len(Item.filter(name="apple")) == 1
    assert Item.filter(name="nope") == []
    got.delete()
    assert Item.get(item.id) is None


def test_sql_injection_neutralized(db):
    evil = "x' OR '1'='1"
    Item.create(name=evil, qty=1)
    assert len(Item.all()) == 1  # table intact, value stored literally
    assert Item.get(Item.all()[0].id).name == evil
    assert Item.filter(name=evil)[0].qty == 1


def test_parameterized_only():
    sql, params = Query("items").where(name="a'b").build_select()
    assert "a'b" not in sql and params == ["a'b"]
    assert "?" in sql
    sql, params = Query("items").build_insert({"name": "x'; DROP--"})
    assert "DROP" not in sql and params == ["x'; DROP--"]
    with pytest.raises(ValueError):
        Query("items; DROP TABLE items--").build_select()
    with pytest.raises(ValueError):
        Query("items").build_update({"name": "x"})
    with pytest.raises(ValueError):
        Query("items").build_delete()


def test_transaction_rollback(db):
    with pytest.raises(RuntimeError):
        with db.transaction():
            Item.create(name="temp")
            raise RuntimeError("boom")
    assert Item.all() == []
    with db.transaction():
        Item.create(name="kept")
    assert len(Item.all()) == 1


def test_migration_up_down(tmp_path):
    outdir = str(tmp_path / "migrations")
    paths = autogen([Item], outdir=outdir)
    assert os.path.exists(paths["up"]) and os.path.exists(paths["down"])
    d = SQLiteDB(":memory:")
    assert apply(d, outdir, "up") >= 1
    Item.bind(d)
    Item.create(name="mig")
    assert len(Item.all()) == 1
    assert apply(d, outdir, "down") >= 1
    with pytest.raises(Exception):
        Item.all()
    d.close()
    Item._db = None


def test_postgres_guard():
    with pytest.raises(RuntimeError, match="psycopg"):
        PostgresDB("postgres://localhost/x")


def test_model_unbound_errors():
    class Orphan(Model):
        __table__ = "orphan"
        id = IntegerField(primary_key=True)

    Orphan._db = None
    with pytest.raises(RuntimeError, match="not bound"):
        Orphan.all()


# ---------------------------------------------------------------- cache


def test_cache_memory_ttl_tags():
    c = MemoryCache()
    c.set("a", 1, tags=("t",))
    c.set("b", 2, tags=("t",))
    assert c.get("a") == 1
    assert c.invalidate_tag("t") == 2
    assert c.get("a") is None and c.get("b") is None


def test_cache_memory_ttl_expiry():
    c = MemoryCache()
    c.set("x", "v", ttl=-1)
    assert c.get("x") is None
    c.set("y", "v", ttl=60)
    assert c.get("y") == "v"
    assert c.delete("y") is True
    assert c.delete("y") is False


def test_cache_redis_guard():
    if "redis" in sys.modules:
        pytest.skip("real redis installed; guard path not applicable")
    with pytest.raises(RuntimeError, match="redis"):
        RedisCache.__new__(RedisCache) and get_cache("redis")
    with pytest.raises(ValueError):
        get_cache("nope")


def test_cache_redis_fake_client():
    class Fake:
        def __init__(self):
            self.kv = {}
            self.sets = {}

        def set(self, k, v, ex=None):
            self.kv[k] = v

        def get(self, k):
            return self.kv.get(k)

        def delete(self, *ks):
            n = 0
            for k in ks:
                n += self.kv.pop(k, None) is not None
            return n

        def sadd(self, t, m):
            self.sets.setdefault(t, set()).add(m)

        def smembers(self, t):
            return self.sets.get(t, set())

        def flushdb(self):
            self.kv.clear()

    c = RedisCache(client=Fake())
    c.set("a", {"n": 1}, tags=("t",))
    assert c.get("a") == {"n": 1}
    assert c.invalidate_tag("t") == 1
    assert c.get("a") is None


# ---------------------------------------------------------------- server


def _rpc_request(name, args=None, headers=None, ip="127.0.0.1"):
    body = json.dumps({"args": args or []}).encode()
    h = {"content-type": "application/json"}
    h.update(headers or {})
    return Request("POST", f"/__pyweb/rpc/{name}", headers=h, body=body, client_ip=ip)


def test_typed_rpc_and_health():
    app = PyWeb()

    @app.rpc()
    def add(a: int, b: int):
        return a + b

    req = _rpc_request("add", [2, 3],
                       headers={"x-csrf-token": app.server.middlewares[3].__class__ and "x"})
    # bypass CSRF by seeding the session token: go through page to set it
    resp = app.handle(Request("GET", "/__pyweb/health"))
    assert resp.status == 200
    assert json.loads(resp.body)["status"] == "ok"
    assert resp.headers["x-request-id"]
    assert resp.headers["content-security-policy"]
    assert resp.headers["x-frame-options"] == "DENY"


def test_csrf_reject_and_accept():
    app = PyWeb()

    @app.rpc()
    def ping():
        return "pong"

    denied = app.handle(_rpc_request("ping"))
    assert denied.status == 403

    # accept: establish session token, then present it
    @app.page("/p", render="static")
    def p():
        return "hi"

    app.handle(Request("GET", "/p"))
    token = None
    # token lives in session set during render; emulate by reading last session:
    # simplest: create session manually via signed cookie roundtrip
    from pyweb.runtime.server import SessionMiddleware

    sess_mw = next(m for m in app.server.middlewares if isinstance(m, SessionMiddleware))
    tok = new_csrf_token()
    cookie = sign_cookie(json.dumps({"csrf": tok}), app.secret)
    ok = app.handle(_rpc_request("ping", headers={"x-csrf-token": tok,
                                                  "cookie": f"pyweb_session={cookie}"}))
    assert ok.status == 200
    assert json.loads(ok.body)["result"] == "pong"


def test_signed_cookie_tamper():
    assert unsign_cookie(sign_cookie("v", "s"), "s") == "v"
    assert unsign_cookie(sign_cookie("v", "s") + "x", "s") is None
    assert unsign_cookie("nosig", "s") is None


def test_security_headers_gzip_request_id():
    app = PyWeb()

    @app.page("/big", render="static")
    def big():
        return "x" * 5000

    r1 = app.handle(Request("GET", "/big"))
    assert r1.headers["x-content-type-options"] == "nosniff"
    assert "content-encoding" not in r1.headers
    r2 = app.handle(Request("GET", "/big", headers={"accept-encoding": "gzip"}))
    assert r2.headers["content-encoding"] == "gzip"
    assert gzip.decompress(r2.body) == r1.body
    assert "x" * 5000 in r1.body.decode()
    assert r1.headers["x-request-id"] != r2.headers["x-request-id"]
    r3 = app.handle(Request("GET", "/big", headers={"x-request-id": "abc123"}))
    assert r3.headers["x-request-id"] == "abc123"


def test_rate_limiting():
    srv = Server(middlewares=[])
    from pyweb.runtime.server import RateLimitMiddleware

    rl = RateLimitMiddleware(limit=2, window=60.0)
    srv.middlewares.append(rl)
    srv.routes.append(("/r", {"GET"}, lambda req: Response.text("ok")))
    assert srv.handle(Request("GET", "/r", client_ip="1.2.3.4")).status == 200
    assert srv.handle(Request("GET", "/r", client_ip="1.2.3.4")).status == 200
    limited = srv.handle(Request("GET", "/r", client_ip="1.2.3.4"))
    assert limited.status == 429
    assert limited.headers["retry-after"]


def test_static_hashing():
    mw = StaticFilesMiddleware({"/static/app.js": b"console.log(1)"})
    hashed = mw.url_for("/static/app.js")
    assert hashed != "/static/app.js" and ".js" in hashed
    srv = Server(middlewares=[mw])
    plain = srv.handle(Request("GET", "/static/app.js"))
    assert plain.headers["cache-control"] == "public, max-age=3600"
    imm = srv.handle(Request("GET", hashed))
    assert imm.status == 200
    assert imm.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert imm.body == b"console.log(1)"
    etag = imm.headers["etag"]
    notmod = srv.handle(Request("GET", hashed, headers={"if-none-match": etag}))
    assert notmod.status == 304


def test_streaming_ssr_chunk_order():
    app = PyWeb()

    @app.page("/s", render="stream", title="Streamed")
    def s():
        return "<p>hello</p>" * 500

    page = app.pages["/s"]
    assert page.render == "stream"
    chunks = list(app.stream_chunks(page, "<p>hello</p>" * 500))
    assert chunks[0].startswith("<!DOCTYPE html><html><head>")
    assert chunks[-1] == "</body></html>"
    assert "<p>hello</p>" in "".join(chunks)
    resp = app.handle(Request("GET", "/s"))
    assert resp.status == 200
    assert resp.body.startswith(b"<!DOCTYPE html>")
    assert b"<title>Streamed</title>" in resp.body

    @app.page("/st", render="static", title="T")
    def st():
        return "body"

    assert app.pages["/st"].render == "static"
    with pytest.raises(ValueError):
        app.page("/bad", render="nope")(lambda: "x")


def test_render_strategies_respected():
    app = PyWeb()

    @app.page("/a", render="static")
    def a():
        return "A"

    @app.page("/b", render="server")
    def b():
        return "B"

    assert app.render_page("/a").body.count(b"A") >= 1
    assert app.render_page("/b").body.count(b"B") >= 1
    assert app.render_page("/missing").status == 404


def test_startup_shutdown_hooks():
    srv = Server()
    calls = []
    srv.on_startup(lambda: calls.append("up"))
    srv.on_shutdown(lambda: calls.append("down"))
    srv.startup()
    assert srv.started and calls == ["up"]
    srv.shutdown()
    assert not srv.started and calls == ["up", "down"]


def test_asgi_health_and_rpc():
    app = PyWeb()

    @app.rpc()
    def echo(msg: str):
        return msg

    asgi = app.asgi()

    async def run(scope, body=b""):
        sent = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(msg):
            sent.append(msg)

        await asgi(scope, receive, send)
        return sent

    def scope(path, method="GET", body=b"", headers=None):
        h = [(b"content-type", b"application/json")]
        for k, v in (headers or {}).items():
            h.append((k.encode(), v.encode()))
        return {"type": "http", "method": method, "path": path,
                "headers": h, "query_string": b"", "client": ("9.9.9.9", 1)}

    sent = asyncio.run(run(scope(HEALTH_PATH)))
    assert sent[0]["status"] == 200

    tok = new_csrf_token()
    cookie = sign_cookie(json.dumps({"csrf": tok}), app.secret)
    sent = asyncio.run(run(
        scope("/__pyweb/rpc/echo", "POST",
              json.dumps({"args": ["hi"]}).encode(),
              {"x-csrf-token": tok, "cookie": f"pyweb_session={cookie}"}),
        json.dumps({"args": ["hi"]}).encode(),
    ))
    assert sent[0]["status"] == 200


def test_todo_example_end_to_end():
    from examples.todo import Todo, make_app

    app = make_app()
    created = Todo.create(title="milk")
    assert created.id is not None
    resp = app.handle(Request("GET", "/"))
    assert resp.status == 200 and b"milk" in resp.body
    assert len(Todo.all()) == 1
    app.todo_db.close()
    Todo._db = None
