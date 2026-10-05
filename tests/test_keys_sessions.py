"""0.4.4: per-purpose keys, secret rotation, and session lifetime / revocation."""

import time

import pytest

from pyweb import auth, keys
from pyweb import context as ctx
from pyweb.compiler import compile_source
from pyweb.runtime.server import Request, Server
from pyweb.testing import TestClient

SECRET = "s" * 40


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("PYWEB_AUTH_SECRET_PREVIOUS", raising=False)
    monkeypatch.setattr(auth, "_versions", None)


def in_request(fn, cookies=None, secret=SECRET):
    rc = ctx.RequestContext(Request("GET", "/", cookies=cookies or {}), auth_secret=secret)
    token = ctx.activate(rc)
    try:
        return fn(), rc
    finally:
        ctx.deactivate(token)


def cookie_value(rc):
    return next(c for c in rc.set_cookies if c.startswith(auth.SESSION_COOKIE)).split(";")[0].split("=", 1)[1]


def test_each_purpose_has_its_own_key():
    assert len({keys.derive(SECRET, p) for p in keys.PURPOSES}) == len(keys.PURPOSES)
    assert keys.derive(SECRET, "session") != SECRET
    signed, rc = in_request(lambda: ctx.session.login(7))
    raw = cookie_value(rc)
    assert auth.verify_session(raw, keys.derive(SECRET, "session"), 3600) is not None
    assert auth.verify_session(raw, SECRET, 3600) is None                 # not signed with the root
    assert auth.verify_session(raw, keys.derive(SECRET, "feed"), 3600) is None


def test_sessions_from_before_the_upgrade_still_work():
    old = auth.issue_session({"sub": 3}, SECRET)                         # how 0.4.3 signed it
    user, _ = in_request(lambda: ctx.session.user(), cookies={auth.SESSION_COOKIE: old})
    assert user["sub"] == 3


def test_rotating_the_secret_keeps_people_signed_in(monkeypatch):
    _, rc = in_request(lambda: ctx.session.login(9), secret="old-" + SECRET)
    raw = cookie_value(rc)
    new = "new-" + SECRET
    assert in_request(lambda: ctx.session.user(), cookies={auth.SESSION_COOKIE: raw}, secret=new)[0] is None
    monkeypatch.setenv("PYWEB_AUTH_SECRET_PREVIOUS", "old-" + SECRET)
    user, _ = in_request(lambda: ctx.session.user(), cookies={auth.SESSION_COOKIE: raw}, secret=new)
    assert user["sub"] == 9


def test_each_login_is_a_new_session():
    a, _ = in_request(lambda: ctx.session.login(1))
    b, _ = in_request(lambda: ctx.session.login(1))
    assert a["sid"] != b["sid"] and a["auth_time"] <= int(time.time())


def test_absolute_lifetime_and_sliding_renewal(monkeypatch):
    start = time.time()
    monkeypatch.setattr(auth.time, "time", lambda: start)
    _, rc = in_request(lambda: ctx.session.login(5))
    raw = cookie_value(rc)
    monkeypatch.setattr(auth.time, "time", lambda: start + 2 * 24 * 3600)     # 2 days later: renewed
    user, rc = in_request(lambda: ctx.session.user(), cookies={auth.SESSION_COOKIE: raw})
    assert user["sub"] == 5 and user["iat"] > start and user["auth_time"] == int(start)
    renewed = cookie_value(rc)
    for day in range(6, 30, 6):                                               # keep visiting every 6 days
        monkeypatch.setattr(auth.time, "time", lambda d=day: start + d * 24 * 3600)
        user, rc = in_request(lambda: ctx.session.user(), cookies={auth.SESSION_COOKIE: renewed})
        assert user is not None, day
        renewed = cookie_value(rc)
    monkeypatch.setattr(auth.time, "time", lambda: start + 31 * 24 * 3600)    # past 30 days: signed out
    assert in_request(lambda: ctx.session.user(), cookies={auth.SESSION_COOKIE: renewed})[0] is None


def test_revoke_user_signs_out_every_session():
    _, rc1 = in_request(lambda: ctx.session.login(4))
    _, rc2 = in_request(lambda: ctx.session.login(4))
    auth.revoke_user(4)
    for rc in (rc1, rc2):
        assert in_request(lambda: ctx.session.user(), cookies={auth.SESSION_COOKIE: cookie_value(rc)})[0] is None
    _, rc3 = in_request(lambda: ctx.session.login(4))                        # a new login works
    assert in_request(lambda: ctx.session.user(), cookies={auth.SESSION_COOKIE: cookie_value(rc3)})[0]["sub"] == 4


def test_rpc_gate_accepts_week_old_sessions_like_pages_do(monkeypatch):
    from pyweb.decorators import auth_required
    APP = "from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <p>x</p>\n"
    srv = Server(compile_source(APP), auth_secret=SECRET)

    @auth_required
    def secret_thing():
        return 1
    srv.register_rpc(secret_thing)
    start = time.time()
    monkeypatch.setattr(auth.time, "time", lambda: start)
    _, rc = in_request(lambda: ctx.session.login(2))
    monkeypatch.setattr(auth.time, "time", lambda: start + 3 * 3600)           # 3 hours later
    res = srv.handle(Request("POST", "/__pyweb/rpc/secret_thing", body=b'{"args": {}}',
                             cookies={auth.SESSION_COOKIE: cookie_value(rc)}))
    assert res.status == 200, res.body


def test_feeds_signed_with_a_previous_secret_still_open(monkeypatch):
    from pyweb import realtime as rt
    feed = rt.make_feed("room", keys.derive("old-" + SECRET, "feed"))
    monkeypatch.setenv("PYWEB_AUTH_SECRET_PREVIOUS", "old-" + SECRET)
    assert rt.read_feed(feed, keys.verify_keys(SECRET, "feed"))[0] == "room"
    assert rt.read_feed(feed, keys.verify_keys(SECRET, "live")) is None


def test_redis_session_versions():
    import os
    url = os.environ.get("PYWEB_TEST_REDIS")
    if not url:
        pytest.skip("set PYWEB_TEST_REDIS to run Redis tests")
    store = auth.use_session_versions(auth.RedisSessionVersions(url, prefix=f"pyweb:test:{time.time()}:"))
    assert store.get(8) == 0
    _, rc = in_request(lambda: ctx.session.login(8))
    auth.revoke_user(8)
    assert store.get(8) == 1
    assert in_request(lambda: ctx.session.user(), cookies={auth.SESSION_COOKIE: cookie_value(rc)})[0] is None


def test_app_login_flow_still_works():
    src = """from pyweb import App, server, session

app = App()


@server
def sign_in(name: str) -> str:
    session.login(name, roles=["member"])
    return session.user()["sub"]


@server
def me() -> str:
    user = session.user()
    return user["sub"] if user else ""


@app.page("/")
def Home():
    <p>hi</p>
"""
    client = TestClient(source=src)
    assert client.rpc("sign_in", name="ada") == "ada"
    assert client.rpc("me") == "ada"
