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


def test_redis_rate_limit_falls_back_when_redis_is_down():
    class Down:
        def pipeline(self):
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


def test_healthz_bodies_are_json(tmp_path):
    (tmp_path / "app.pyweb").write_text("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <p>x</p>\n")
    site = Site(str(tmp_path / "app.pyweb"))
    for path in ("/healthz", "/readyz"):
        status, headers, body = site.respond("GET", path, {})
        assert status == 200 and json.loads(body)["ok"] is True
        assert dict(headers)["Cache-Control"] == "no-store"
