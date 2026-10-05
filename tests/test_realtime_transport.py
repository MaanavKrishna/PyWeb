"""Live updates: signed feeds, the SSE stream, the poll fallback and ASGI streaming."""

import asyncio
import json
import threading
import time

from pyweb import context as ctx
from pyweb import realtime as rt
from pyweb.compiler import compile_source
from pyweb.runtime.server import Request, Server

APP = "from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <h1>Chat</h1>\n"
SECRET = "test-secret"


def _server():
    srv = Server(compile_source(APP), auth_secret=SECRET)
    srv.bus = rt.Bus()
    return srv


def _feed(name="chat"):
    return rt.make_feed(name, SECRET)


def test_bus_publish_and_since():
    bus = rt.Bus()
    bus.publish("chat", {"text": "hi"})
    bus.publish("chat", {"text": "yo"})
    entries = bus.since("chat", 0)
    assert [m for _, m in entries] == [{"text": "hi"}, {"text": "yo"}]
    assert bus.since("chat", entries[-1][0]) == []


def test_sse_format_frame():
    frame = rt.sse_format(7, "chat", {"text": "hi"})
    assert frame.startswith("id: 7\nevent: chat\ndata: ")
    assert json.loads(frame.split("data: ", 1)[1]) == {"text": "hi"}


def test_feeds_are_signed_and_expire():
    token = rt.make_feed("room:1", SECRET, max_age=60, now=1000)
    assert rt.open_feed(token, SECRET, now=1030) == "room:1"
    assert rt.open_feed(token, SECRET, now=1061) is None            # expired
    assert rt.open_feed(token, "other-secret", now=1030) is None    # wrong key
    payload, _, sig = token.partition(".")
    forged = rt._b64(json.dumps(["room:2", 9999]).encode()) + "." + sig
    assert rt.open_feed(forged, SECRET, now=1030) is None           # tampered
    assert rt.open_feed("garbage", SECRET) is None


def test_channel_signs_with_the_request_secret_and_remembers_position():
    old = rt.current_bus()
    bus = rt.use_bus(rt.Bus())
    bus.publish("chat", {"text": "before render"})
    rc = ctx.RequestContext(Request("GET", "/"), auth_secret=SECRET)
    token = ctx.activate(rc)
    try:
        feed = rt.channel("chat")
    finally:
        ctx.deactivate(token)
    try:
        from pyweb import keys
        assert rt.read_feed(feed, keys.derive(SECRET, "feed")) == ("chat", bus._seq)   # its own key
        assert rt.read_feed(feed, SECRET) is None                                      # not the root
        bus.publish("chat", {"text": "after render"})
        srv = Server(compile_source(APP), auth_secret=SECRET)
        body = srv.handle(Request("GET", f"/__pyweb/events?feed={feed}")).body.snapshot().decode()
        assert "after render" in body and "before render" not in body
        polled = json.loads(srv.handle(Request("GET", f"/__pyweb/poll?feed={feed}&since=0")).body)
        assert [m["data"]["text"] for m in polled["messages"]] == ["after render"]
    finally:
        rt.use_bus(old)


def test_sse_endpoint_returns_a_stream_that_resumes():
    srv = _server()
    srv.bus.publish("chat", {"text": "old"})
    last = srv.bus._seq
    srv.bus.publish("chat", {"text": "new"})
    res = srv.handle(Request("GET", f"/__pyweb/events?feed={_feed()}", headers={"Last-Event-ID": str(last)}))
    assert res.status == 200 and res.headers["Content-Type"] == "text/event-stream"
    body = res.body.snapshot().decode()
    assert '"new"' in body and '"old"' not in body and f"id: {last + 1}" in body


def test_stream_delivers_live_messages_and_heartbeats():
    bus = rt.Bus()
    stream = rt.EventStream(bus, "chat", heartbeat=0.05, tick=0.01, max_age=5)
    got = []

    def consume():
        for chunk in stream:
            got.append(chunk.decode())
            if any("live" in c for c in got) and any(c.startswith(": ping") for c in got):
                break

    t = threading.Thread(target=consume)
    t.start()
    time.sleep(0.1)
    bus.publish("chat", {"text": "live"})
    t.join(2)
    stream.close()
    assert not t.is_alive()
    assert got[0] == "retry: 2000\n\n" and any('"live"' in c for c in got)


def test_stream_ends_after_max_age():
    chunks = list(rt.EventStream(rt.Bus(), "chat", tick=0.01, max_age=0.05))
    assert chunks[0].startswith(b"retry:")


def test_events_and_poll_reject_missing_or_forged_feeds():
    srv = _server()
    assert srv.handle(Request("GET", "/__pyweb/events")).status == 400
    assert srv.handle(Request("GET", "/__pyweb/events?channel=chat")).status == 400
    assert srv.handle(Request("GET", "/__pyweb/events?feed=" + rt.make_feed("chat", "nope"))).status == 403
    assert srv.handle(Request("GET", "/__pyweb/poll?feed=x.y")).status == 403


def test_poll_fallback_returns_json():
    srv = _server()
    srv.bus.publish("chat", {"text": "hello"})
    srv.bus.publish("other", {"text": "not for you"})
    res = srv.handle(Request("GET", f"/__pyweb/poll?feed={_feed()}&since=0"))
    body = json.loads(res.body)
    assert [m["data"] for m in body["messages"]] == [{"text": "hello"}]
    res2 = srv.handle(Request("GET", f"/__pyweb/poll?feed={_feed()}&since={body['last_id']}"))
    assert json.loads(res2.body)["messages"] == []


def test_publish_uses_the_configured_bus():
    old = rt.current_bus()
    bus = rt.use_bus(rt.Bus())
    try:
        rt.publish("chat", {"n": 1})
        assert [m for _, m in bus.since("chat")] == [{"n": 1}]
    finally:
        rt.use_bus(old)


def test_asgi_streams_until_disconnect(tmp_path):
    from pyweb.asgi import create_app
    (tmp_path / "app.pyweb").write_text(APP)
    app = create_app(str(tmp_path / "app.pyweb"), auth_secret=SECRET, rate_limit=False)
    old = rt.current_bus()
    rt.use_bus(rt.Bus())
    sent, disconnect = [], asyncio.Event()

    async def receive():
        if not sent:
            return {"type": "http.request", "body": b""}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)
        if msg["type"] == "http.response.body" and b"live" in msg.get("body", b""):
            disconnect.set()

    async def run():
        scope = {"type": "http", "method": "GET", "path": "/__pyweb/events",
                 "query_string": f"feed={_feed()}".encode(), "headers": []}
        task = asyncio.ensure_future(app(scope, receive, send))
        await asyncio.sleep(0.2)
        rt.publish("chat", {"text": "live"})
        await asyncio.wait_for(task, 5)

    try:
        asyncio.run(run())
    finally:
        rt.use_bus(old)
    assert sent[0]["status"] == 200
    assert any(b'"live"' in m.get("body", b"") for m in sent[1:])
    assert sent[-1] == {"type": "http.response.body", "body": b""}


def test_browser_runtime_exports_subscribe():
    from pathlib import Path
    src = Path("pyweb/runtime/browser/runtime.js").read_text()
    assert "export function subscribe(feed" in src
    assert "/__pyweb/events?${q}" in src and "/__pyweb/poll?${q}" in src and "feed=${" in src


def test_bus_forgets_quiet_channels(monkeypatch):
    from pyweb import realtime
    now = [1000.0]
    monkeypatch.setattr(realtime.time, "monotonic", lambda: now[0])
    bus = realtime.Bus()
    kept = bus.channel("room:kept")
    unsub = kept.subscribe(lambda _m: None)
    for i in range(300):
        bus.publish(f"room:{i}", {"n": i})
    bus.publish("room:kept", {"n": 1})
    assert len(bus.since("room:5", 0)) == 1
    for i in range(realtime.LOG_SIZE + 50):
        bus.publish("room:busy", i)
    assert len(bus.since("room:busy", 0, limit=1000)) == realtime.LOG_SIZE   # only the latest are kept
    now[0] += realtime.IDLE_CHANNEL_SECONDS + 1
    for i in range(256):
        bus.publish("room:new", i)
    assert bus.since("room:5", 0) == [] and "room:5" not in bus.channels
    assert bus.since("room:kept", 0)                 # someone is still listening
    unsub()
