"""QA-005: realtime transport contract — SSE + poll fallback."""

import json

from pyweb import realtime as rt
from pyweb.compiler import compile_source
from pyweb.runtime.server import Request, Server

APP = "from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <h1>Chat</h1>\n"


def _server():
    srv = Server(compile_source(APP))
    srv.bus = rt.Bus()
    return srv


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


def test_sse_endpoint_streams_buffered_frames():
    srv = _server()
    srv.bus.publish("chat", {"text": "hello"})
    res = srv.handle(Request("GET", "/__pyweb/events?channel=chat"))
    assert res.status == 200
    assert res.headers["Content-Type"] == "text/event-stream"
    assert "event: chat" in res.body and '"hello"' in res.body


def test_sse_resume_skips_seen_ids():
    srv = _server()
    srv.bus.publish("chat", {"text": "old"})
    last = srv.bus._seq
    srv.bus.publish("chat", {"text": "new"})
    res = srv.handle(Request("GET", "/__pyweb/events?channel=chat",
                             headers={"Last-Event-ID": str(last)}))
    assert '"new"' in res.body and '"old"' not in res.body


def test_sse_requires_channel():
    assert _server().handle(Request("GET", "/__pyweb/events")).status == 400


def test_poll_fallback_returns_json():
    srv = _server()
    srv.bus.publish("chat", {"text": "hello"})
    res = srv.handle(Request("GET", "/__pyweb/poll?channel=chat&since=0"))
    assert res.status == 200
    body = json.loads(res.body)
    assert body["messages"][0]["data"] == {"text": "hello"}
    res2 = srv.handle(Request(
        "GET", f"/__pyweb/poll?channel=chat&since={body['last_id']}"))
    assert json.loads(res2.body)["messages"] == []


def test_poll_requires_channel():
    assert _server().handle(Request("GET", "/__pyweb/poll")).status == 400


def test_browser_runtime_exports_subscribe():
    import re
    from pathlib import Path
    src = Path("pyweb/runtime/browser/runtime.js").read_text()
    assert re.search(r"export function subscribe\(channel", src)
    assert "/__pyweb/events" in src and "/__pyweb/poll" in src
