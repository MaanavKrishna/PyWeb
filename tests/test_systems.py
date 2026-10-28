"""0.4.4: settings, shared rate limits, readiness, render timeouts, shutdown, safe cache."""

import json
import os
import time

import pytest

from pyweb import config, jobs, realtime, rpc
from pyweb.cache import RedisCache
from pyweb.hosting import Site, health, shutdown

REDIS = os.environ.get("PYWEB_TEST_REDIS")


def test_settings_parse_and_validate():
    s = config.settings({"PYWEB_COOKIE_SECURE": "true", "PYWEB_TRUST_PROXY": "2",
                         "PYWEB_AUTH_SECRET_PREVIOUS": "a, b,", "PYWEB_MAX_CONNECTIONS": "64"})
    assert s.cookie_secure and s.trust_proxy == 2 and s.previous_secrets == ("a", "b")
    assert s.max_connections == 64 and s.render_timeout == 30 and not s.production
    for env, name in [({"PYWEB_MAX_CONNECTIONS": "lots"}, "PYWEB_MAX_CONNECTIONS"),
                      ({"PYWEB_COOKIE_SECURE": "maybe"}, "PYWEB_COOKIE_SECURE"),
                      ({"PYWEB_SOCKET_TIMEOUT": "0"}, "PYWEB_SOCKET_TIMEOUT")]:
        with pytest.raises(ValueError, match=name):
            config.settings(env)


def test_bad_settings_stop_the_server_at_startup(tmp_path, monkeypatch):
    (tmp_path / "app.pyweb").write_text("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <p>x</p>\n")
    monkeypatch.setenv("PYWEB_MAX_CONNECTIONS", "lots")
    with pytest.raises(ValueError, match="PYWEB_MAX_CONNECTIONS"):
        Site(str(tmp_path / "app.pyweb"))


@pytest.mark.skipif(not REDIS, reason="set PYWEB_TEST_REDIS to run Redis tests")
def test_redis_rate_limit_is_shared_between_processes():
    prefix = f"pyweb:test:rl:{time.time()}:"
    a = rpc.RedisRateLimiter(REDIS, max_calls=2, window=60, prefix=prefix)
    b = rpc.RedisRateLimiter(REDIS, max_calls=2, window=60, prefix=prefix)   # "another server"
    assert a.allow("f:1.2.3.4")[0] and b.allow("f:1.2.3.4")[0]
    ok, retry = a.allow("f:1.2.3.4")
    assert not ok and 1 <= retry <= 61


@pytest.mark.skipif(not REDIS, reason="set PYWEB_TEST_REDIS to run Redis tests")
def test_redis_rate_limit_holds_under_concurrent_calls():
    from concurrent.futures import ThreadPoolExecutor
    prefix = f"pyweb:test:rl:{time.time()}:"
    lims = [rpc.RedisRateLimiter(REDIS, max_calls=10, window=60, prefix=prefix) for _ in range(4)]
    with ThreadPoolExecutor(16) as ex:
        got = list(ex.map(lambda i: lims[i % 4].allow("hot")[0], range(80)))
    assert sum(got) == 10


def test_redis_errors_pause_redis_for_a_while_not_forever(monkeypatch):
    from pyweb import redisutil
    calls = []

    class Flaky:
        def eval(self, *a):
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("blip")
            return [1, b"0"]
    lim = rpc.RedisRateLimiter(client=Flaky(), max_calls=5, window=60)
    clock = [1000.0]
    monkeypatch.setattr(redisutil.time, "monotonic", lambda: clock[0])
    assert lim.allow("k")[0] and len(calls) == 1          # failed: limited in this process
    assert lim.allow("k")[0] and len(calls) == 1          # Redis is left alone for a few seconds
    clock[0] += redisutil.RETRY_AFTER + 0.1
    assert lim.allow("k")[0] and len(calls) == 2          # then tried again, and used
    assert lim._broken.error is None


def test_redis_rate_limit_falls_back_when_redis_is_down():
    class Down:
        def eval(self, *a):
            raise ConnectionError("no redis")
    lim = rpc.RedisRateLimiter(client=Down(), max_calls=1, window=60)
    assert lim.allow("k")[0] and not lim.allow("k")[0]          # still limited, in this process


def test_default_limiter_uses_redis_when_configured(monkeypatch):
    monkeypatch.delenv("PYWEB_REDIS_URL", raising=False)
    assert type(rpc.default_rate_limiter()) is rpc.RateLimiter
    if REDIS:
        monkeypatch.setenv("PYWEB_REDIS_URL", REDIS)
        assert isinstance(rpc.default_rate_limiter(), rpc.RedisRateLimiter)


def test_readyz_checks_databases(tmp_path):
    from pyweb.db import connect
    db = connect(f"sqlite:///{tmp_path / 'a.db'}")
    code, payload = health(ready=True)
    assert code == 200 and payload["ok"] and "ok" in payload["checks"].values()

    def broken():
        raise RuntimeError("database is down")
    db.ping = broken
    code, payload = health(ready=True)
    assert code == 503 and not payload["ok"]
    assert health(ready=False)[0] == 200                        # liveness doesn't depend on the database
    del db


def test_slow_pages_answer_504(tmp_path, monkeypatch):
    (tmp_path / "app.pyweb").write_text(
        "import time\nfrom pyweb import App\napp=App()\n@app.page('/')\ndef H():\n"
        "    time.sleep(3)\n    <p>x</p>\n@app.page('/fast')\ndef F():\n    <p>fast</p>\n")
    monkeypatch.setenv("PYWEB_RENDER_TIMEOUT", "1")
    site = Site(str(tmp_path / "app.pyweb"))
    start = time.monotonic()
    status, _, body = site.respond("GET", "/", {})
    assert status == 504 and time.monotonic() - start < 2.5
    assert site.respond("GET", "/fast", {})[0] == 200


def test_shutdown_ends_streams_and_waits_for_jobs():
    bus = realtime.Bus()
    stream = realtime.EventStream(bus, "room", tick=0.05)
    it = iter(stream)
    next(it)                                                     # the stream is open
    done = []
    jobs._default_queue.submit(lambda: (time.sleep(0.2), done.append(1)))
    closed, unfinished = shutdown(timeout=5)
    assert closed >= 1 and unfinished == 0 and done == [1]
    assert list(it) == []                                        # the stream ended


class FakeRedis:
    def __init__(self):
        self.data = {}

    def set(self, k, v, ex=None):
        self.data[k] = v

    def get(self, k):
        return self.data.get(k)

    def sadd(self, *a):
        pass


def test_redis_cache_refuses_pickle_unless_allowed():
    import pickle
    r = FakeRedis()
    cache = RedisCache(client=r)
    cache.set("k", {"a": [1, 2]})
    assert cache.get("k") == {"a": [1, 2]}
    with pytest.raises(TypeError, match="JSON"):
        cache.set("obj", {1, 2})
    r.data["pyweb:k:evil"] = b"p:" + pickle.dumps({"x": 1})       # written by someone else
    assert cache.get("evil", "miss") == "miss"                   # never unpickled
    trusting = RedisCache(client=r, allow_pickle=True)
    trusting.set("obj", {1, 2})
    assert trusting.get("obj") == {1, 2}


@pytest.mark.skipif(not REDIS, reason="set PYWEB_TEST_REDIS to run Redis tests")
def test_redis_cache_clears_only_its_own_keys_and_works_with_the_decorator():
    import redis

    from pyweb.cache import cache as cached
    from pyweb.cache import swr
    r = redis.Redis.from_url(REDIS)
    prefix = f"pyweb:test:cache:{time.time()}:"
    r.set(prefix + "not-the-cache", b"keep")
    store = RedisCache(REDIS, prefix=prefix)
    store.set("a", 1, ttl=30, tags=["t"])
    assert 0 < r.ttl(prefix + "tag:t") <= 30                     # tags expire with their keys
    calls = []

    @cached(minutes=1, backend=store)
    def double(n):
        calls.append(n)
        return n * 2
    assert double(2) == 4 and double(2) == 4 and calls == [2]
    double.cache_invalidate()
    assert double(2) == 4 and calls == [2, 2] and store.get("a") == 1   # only that function's entries
    assert swr(store, "s", lambda: "v")[1] == "miss" and swr(store, "s", lambda: "v")[1] == "fresh"
    store.clear()
    assert store.get("a") is None and r.get(prefix + "not-the-cache") == b"keep"
    r.delete(prefix + "not-the-cache")


def test_healthz_bodies_are_json(tmp_path):
    (tmp_path / "app.pyweb").write_text("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <p>x</p>\n")
    site = Site(str(tmp_path / "app.pyweb"))
    for path in ("/healthz", "/readyz"):
        status, headers, body = site.respond("GET", path, {})
        assert status == 200 and json.loads(body)["ok"] is True
        assert dict(headers)["Cache-Control"] == "no-store"


def test_check_production(tmp_path, monkeypatch, capsys):
    import argparse

    from pyweb import cli
    (tmp_path / "app.pyweb").write_text("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <p>x</p>\n")
    for var in ("PYWEB_AUTH_SECRET", "PYWEB_ENV", "PYWEB_COOKIE_SECURE", "PYWEB_TRUST_PROXY", "PYWEB_REDIS_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    args = argparse.Namespace(file=str(tmp_path / "app.pyweb"), production=True)
    with pytest.raises(SystemExit):
        cli.cmd_check(args)
    out = capsys.readouterr().out
    for word in ("PYWEB_AUTH_SECRET", "PYWEB_ENV", "PYWEB_COOKIE_SECURE", "PYWEB_TRUST_PROXY", "WEB_CONCURRENCY=4"):
        assert word in out
    monkeypatch.setenv("PYWEB_AUTH_SECRET", "short")
    assert any("shorter than 32" in p for p in cli.production_problems())
    for var, value in (("PYWEB_AUTH_SECRET", "k" * 40), ("PYWEB_ENV", "production"), ("PYWEB_COOKIE_SECURE", "1"),
                       ("PYWEB_TRUST_PROXY", "1"), ("WEB_CONCURRENCY", "1")):
        monkeypatch.setenv(var, value)
    cli.cmd_check(args)                                          # no SystemExit
    assert "production: ready" in capsys.readouterr().out


def test_time_limits_over_pyweb_server_use_one_thread(tmp_path, monkeypatch):
    """PyWeb's server enforces the limits itself: a slow page or function still gets a 504 on
    time, other requests keep working, and no second pool sits between them."""
    import json as _json
    import threading as _threading
    import urllib.error
    import urllib.request

    from pyweb.net.server import Running
    from pyweb.runtime import server as rt
    (tmp_path / "app.pyweb").write_text(
        "import time\nfrom pyweb import App, server\napp=App()\n"
        "@server\ndef slow() -> int:\n    time.sleep(3)\n    return 1\n"
        "@app.page('/')\ndef H():\n    time.sleep(3)\n    <p>x</p>\n"
        "@app.page('/fast')\ndef F():\n    <p>fast</p>\n")
    monkeypatch.setenv("PYWEB_RENDER_TIMEOUT", "1")
    calls = []
    monkeypatch.setattr(rt, "_render_pool", lambda: calls.append("render") or None)
    monkeypatch.setattr(rt, "_rpc_pool", lambda: calls.append("rpc") or None)
    site = Site(str(tmp_path / "app.pyweb"), rpc_timeout=1, rate_limit=False)
    running = Running(site)
    try:
        def get(path, data=None):
            req = urllib.request.Request(running.url + path, data=data, headers={
                "Content-Type": "application/json", "Origin": running.url} if data else {})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read()
        start = time.monotonic()
        results = {}
        t = _threading.Thread(target=lambda: results.update(page=get("/")))
        t.start()
        assert get("/fast")[0] == 200                                # not blocked by the slow page
        t.join()
        status, body = results["page"]
        assert status == 504 and b"took too long" in body and time.monotonic() - start < 2.5
        status, body = get("/__pyweb/rpc/slow", _json.dumps({"args": {}}).encode())
        assert status == 504 and _json.loads(body)["error"]["code"] == "timeout"
        assert calls == []                                           # neither extra pool was used
    finally:
        running.stop(timeout=0.5)


def test_many_threads_render_and_call_at_once(tmp_path, monkeypatch):
    """Pages and server functions from 8 threads at once (on a free-threaded Python they really
    run in parallel): every response is right and every write lands exactly once."""
    import threading as _threading

    from pyweb.testing import TestClient
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYWEB_WORKER", "0")
    (tmp_path / "app.pyweb").write_text(
        "from pyweb import App, Field, Model, server\n"
        "app = App(database='sqlite:///t.db')\n\n\n"
        "class Note(Model):\n    body: str = Field(max=40)\n\n\n"
        "@server\ndef add(body: str) -> int:\n    return Note.create(body=body).id\n\n\n"
        "@app.page('/')\ndef Home(q: str = ''):\n    notes = Note.query().order('id').limit(5).all()\n"
        "    <h1>{q}</h1>\n    <ul>\n        for n in notes:\n            <li>{n['body']}</li>\n    </ul>\n")
    client = TestClient(str(tmp_path / "app.pyweb"))
    errors, ids = [], []

    def work(t):
        try:
            for i in range(40):
                page = client.get(f"/?q=t{t}-{i}")
                assert page.status == 200 and f"<h1>t{t}-{i}</h1>" in page.text
                ids.append(client.rpc("add", body=f"{t}-{i}"))
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))
    threads = [_threading.Thread(target=work, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(ids) == len(set(ids)) == 320
