"""Production RPC: typed errors, auth/CSRF gate, rate limits, traces."""

import json

from pyweb import auth, rpc
from pyweb.compiler import compile_source
from pyweb.runtime.server import Request, Server
from pyweb.keys import derive  # tokens are signed with purpose keys, never the raw secret

APP = ("from pyweb import App\napp=App()\n@app.page('/')\n"
       "def H():\n    v = 1\n    <p>{v}</p>\n")


def _server(**kw):
    return Server(compile_source(APP), **kw)


def _post(server, name, args=None, **kw):
    return server.handle(Request(
        "POST", f"/__pyweb/rpc/{name}",
        body=json.dumps({"args": args or {}}).encode(), **kw))


def test_typed_error_envelope_and_codes():
    s = _server()
    def boom():
        raise rpc.RPCError(rpc.Code.CONFLICT, "version mismatch",
                           details={"expected": 3})
    s.register_rpc(boom)
    res = _post(s, "boom")
    assert res.status == 409
    body = json.loads(res.body)
    assert body["error"]["code"] == "conflict"
    assert body["error"]["details"] == {"expected": 3}
    assert "X-Request-Id" in res.headers and "traceparent" in res.headers


def test_traceparent_propagates():
    s = _server()
    s.register_rpc(lambda: 1).__name__ if False else None
    def ping():
        return "pong"
    s.register_rpc(ping)
    res = _post(s, "ping", headers={
        "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"})
    assert res.headers["traceparent"].startswith(
        "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-")
    assert "X-Request-Id" in res.headers


def test_unknown_rpc_is_typed_404():
    res = _post(_server(), "nope")
    assert res.status == 404
    assert json.loads(res.body)["error"]["code"] == "not_found"


def test_invalid_json_is_validation_error():
    s = _server()
    def f(x: int):
        return x
    s.register_rpc(f)
    res = s.handle(Request("POST", "/__pyweb/rpc/f", body=b"{bad"))
    assert res.status == 422
    assert json.loads(res.body)["error"]["code"] == "validation_error"


def test_auth_gate_blocks_anonymous():
    from pyweb.decorators import auth_required
    s = _server(auth_secret="s3cret")
    @auth_required
    def secret_fn():
        return "classified"
    s.register_rpc(secret_fn)
    assert _post(s, "secret_fn").status == 401
    token = auth.issue_session({"sub": "u1"}, derive("s3cret", "session"))
    res = _post(s, "secret_fn", headers={"Authorization": f"Bearer {token}"})
    assert res.status == 200


def test_csrf_gate_blocks_cookie_calls_without_token():
    from pyweb.decorators import auth_required
    s = _server(auth_secret="s3cret", csrf_secret="csrf-s")
    @auth_required
    def mut():
        return "ok"
    s.register_rpc(mut)
    token = auth.issue_session({"sub": "u1", "sid": "sess1"}, derive("s3cret", "session"))
    denied = _post(s, "mut", cookies={"pyweb_session": token})
    assert denied.status == 403
    assert json.loads(denied.body)["error"]["code"] == "csrf_failed"
    good = _post(s, "mut", cookies={"pyweb_session": token},
                 headers={"X-CSRF-Token": auth.csrf_token("csrf-s", "sess1")})
    assert good.status == 200


def test_rate_limit_returns_429_with_retry_after():
    s = _server(rate_limit=rpc.RateLimiter(max_calls=2, window=60.0))
    def f():
        return 1
    s.register_rpc(f)
    assert _post(s, "f").status == 200
    assert _post(s, "f").status == 200
    limited = _post(s, "f")
    assert limited.status == 429
    assert json.loads(limited.body)["error"]["code"] == "rate_limited"
    assert "Retry-After" in limited.headers


def test_timeout_maps_to_504():
    import time as _t
    s = _server(rpc_timeout=0.05)
    def slow():
        _t.sleep(0.5)
        return 1
    s.register_rpc(slow)
    res = _post(s, "slow")
    assert res.status == 504
    assert json.loads(res.body)["error"]["code"] == "timeout"


def test_streaming_rpc_ndjson():
    s = _server()
    def gen():
        return {"__pyweb_stream__": True, "chunks": ["a", "b"]}
    s.register_rpc(gen)
    res = _post(s, "gen")
    assert res.headers["Content-Type"] == "application/x-ndjson"
    assert res.body.splitlines() == ['{"chunk": "a"}', '{"chunk": "b"}']


def test_retry_policy_backoff_and_retryable():
    p = rpc.RetryPolicy(attempts=3, base_delay=0.1, jitter=0.0)
    assert p.should_retry(0, 503) and p.should_retry(1, 503)
    assert not p.should_retry(2, 503) and not p.should_retry(0, 422)
    assert p.delay(0) <= p.delay(1) <= 2.0


def test_rate_limit_is_per_client_address_and_ignores_spoofed_headers(monkeypatch):
    monkeypatch.delenv("PYWEB_TRUST_PROXY", raising=False)
    s = _server(rate_limit=rpc.RateLimiter(max_calls=1, window=60.0))
    def f():
        return 1
    s.register_rpc(f)
    assert _post(s, "f", client="10.0.0.1").status == 200
    # A new X-Forwarded-For value per call no longer gets around the limit...
    assert _post(s, "f", client="10.0.0.1", headers={"X-Forwarded-For": "1.2.3.4"}).status == 429
    # ...and one busy visitor doesn't use up everyone else's allowance.
    assert _post(s, "f", client="10.0.0.2").status == 200


def test_trusted_proxy_uses_the_address_it_saw(monkeypatch):
    from pyweb.runtime.server import client_ip
    req = Request("POST", "/", headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.9"}, client="10.0.0.5")
    monkeypatch.delenv("PYWEB_TRUST_PROXY", raising=False)
    assert client_ip(req) == "10.0.0.5"
    monkeypatch.setenv("PYWEB_TRUST_PROXY", "1")
    assert client_ip(req) == "203.0.113.9"          # added by our proxy, not the client's claim
    monkeypatch.setenv("PYWEB_TRUST_PROXY", "2")
    assert client_ip(req) == "6.6.6.6"


def test_rate_limiter_forgets_idle_callers():
    now = [0.0]
    lim = rpc.RateLimiter(max_calls=1, window=10.0, time_fn=lambda: now[0])
    for i in range(500):
        lim.allow(f"caller-{i}")
    assert not lim.allow("caller-0")[0]
    now[0] = 11.0
    assert lim.allow("caller-0")[0]
    assert len(lim._hits) == 1                       # the other 499 were swept


def test_async_functions_respect_the_timeout():
    import asyncio
    s = _server(rpc_timeout=0.05)
    async def slow():
        await asyncio.sleep(1)
    s.register_rpc(slow)
    res = _post(s, "slow")
    assert res.status == 504 and json.loads(res.body)["error"]["code"] == "timeout"
