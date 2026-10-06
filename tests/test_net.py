"""PyWeb's own server: the HTTP/1.1 parser (smuggling vectors, fuzzing), the WebSocket
protocol (RFC 6455 rules, fuzzing), and the running server end to end."""

import base64
import json
import re
import socket
import time
import urllib.request

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pyweb.net import http as H
from pyweb.net import ws as W
from wsclient import Client  # noqa: F401 - used below

# ------------------------------------------------------------------ parser


def head(*lines):
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def test_parses_a_normal_request():
    h = H.parse_head(head("POST /x?y=1 HTTP/1.1", "Host: a", "Content-Length: 5", "Cookie: a=1", "cookie: b=2"))
    assert (h.method, h.target, h.length, h.chunked, h.keep_alive) == ("POST", "/x?y=1", 5, False, True)
    assert h.headers["cookie"] == "a=1; b=2" and h.header_dict()["Content-Length"] == "5"


@pytest.mark.parametrize("lines,status", [
    (["POST / HTTP/1.1", "Host: a", "Content-Length: 5", "Transfer-Encoding: chunked"], 400),   # CL.TE
    (["POST / HTTP/1.1", "Host: a", "Content-Length: 5", "Content-Length: 6"], 400),
    (["POST / HTTP/1.1", "Host: a", "Content-Length: 5", "Content-Length: 5"], 400),
    (["POST / HTTP/1.1", "Host: a", "Content-Length: +5"], 400),
    (["POST / HTTP/1.1", "Host: a", "Content-Length: 0x5"], 400),
    (["POST / HTTP/1.1", "Host: a", "Content-Length: 5 5"], 400),
    (["POST / HTTP/1.1", "Host: a", "Transfer-Encoding: gzip, chunked"], 501),
    (["POST / HTTP/1.1", "Host: a", "Transfer-Encoding: chunked", "Transfer-Encoding: chunked"], 501),
    (["POST / HTTP/1.1", "Host: a", "Transfer-Encoding: xchunked"], 501),
    (["POST / HTTP/1.0", "Transfer-Encoding: chunked"], 400),
    (["POST / HTTP/1.1", "Host: a", "Transfer-Encoding : chunked"], 400),                      # space before colon
    (["GET / HTTP/1.1", "Host: a", "X-A: 1", " folded"], 400),
    (["GET / HTTP/1.1", "Host: a", "X-A: a\x00b"], 400),
    (["GET / HTTP/1.1"], 400),                                                                   # no Host
    (["GET / HTTP/1.1", "Host: a", "Host: b"], 400),
    (["GET http://evil/ HTTP/1.1", "Host: a"], 400),
    (["GET / HTTP/2.0", "Host: a"], 505),
    (["GET  / HTTP/1.1", "Host: a"], 400),
    (["G(T / HTTP/1.1", "Host: a"], 400),
    (["GET / HTTP/1.1", "Host: a", "Expect: something"], 417),
    (["GET /" + "a" * 9000 + " HTTP/1.1", "Host: a"], 414),
    (["GET / HTTP/1.1", "Host: a"] + [f"X-{i}: 1" for i in range(101)], 431),
])
def test_refuses_ambiguous_or_malformed_requests(lines, status):
    with pytest.raises(H.HTTPError) as err:
        H.parse_head(head(*lines))
    assert err.value.status == status


def test_refuses_bare_lf():
    with pytest.raises(H.HTTPError):
        H.parse_head(b"GET / HTTP/1.1\r\nHost: a\nX: 1\r\n\r\n")


def test_keep_alive_rules():
    assert not H.parse_head(head("GET / HTTP/1.1", "Host: a", "Connection: close")).keep_alive
    assert not H.parse_head(head("GET / HTTP/1.0")).keep_alive
    assert H.parse_head(head("GET / HTTP/1.0", "Connection: keep-alive")).keep_alive


@settings(max_examples=1500, deadline=None)
@given(st.binary(max_size=300))
def test_parser_fuzz_only_raises_http_errors(blob):
    try:
        H.parse_head(blob + b"\r\n\r\n")
    except H.HTTPError:
        pass


@settings(max_examples=500, deadline=None)
@given(st.lists(st.binary(min_size=1, max_size=40), max_size=8), st.lists(st.integers(1, 7), min_size=1))
def test_chunked_bodies_decode_however_they_are_split(chunks, cuts):
    wire = b"".join(b"%x;ext=1\r\n%s\r\n" % (len(c), c) for c in chunks) + b"0\r\nX-T: 1\r\n\r\nNEXT"
    d = H.ChunkedDecoder(10_000)
    out, i, k = b"", 0, 0
    while not d.done and i < len(wire):
        step = cuts[k % len(cuts)]
        out += d.feed(wire[i:i + step])
        i, k = i + step, k + 1
    assert d.done and out == b"".join(chunks)
    assert (d.leftover() + wire[i:]) == b"NEXT"


@pytest.mark.parametrize("wire", [b"-1\r\n", b"0x5\r\n", b"fffffffffffffffff\r\n", b"5\r\nabcdeXX", b"zz\r\n"])
def test_bad_chunks_are_refused(wire):
    with pytest.raises(H.HTTPError):
        H.ChunkedDecoder(10_000).feed(wire)


def test_chunked_body_limit():
    with pytest.raises(H.HTTPError) as err:
        H.ChunkedDecoder(10).feed(b"b\r\n")
    assert err.value.status == 413


def test_response_head_refuses_header_injection():
    with pytest.raises(ValueError):
        H.response_head(200, [("Location", "/a\r\nSet-Cookie: x=1")])


# --------------------------------------------------------------- websocket

MASK = b"\x37\xfa\x21\x3d"


def test_accept_key_matches_the_rfc_example():
    assert W.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_rfc_6455_masked_hello():
    assert [e.data for e in W.Parser().feed(bytes.fromhex("818537fa213d7f9f4d5158"))] == ["Hello"]


def test_fragments_and_interleaved_ping():
    p = W.Parser()
    wire = (W.frame(W.TEXT, b"Hel", fin=False, mask=MASK) + W.frame(W.PING, b"x", mask=MASK) +
            W.frame(W.CONT, b"lo", mask=MASK))
    events = []
    for i in range(len(wire)):                                  # one byte at a time
        events += p.feed(wire[i:i + 1])
    assert isinstance(events[0], W.Ping) and events[1].data == "Hello"


@pytest.mark.parametrize("wire,code", [
    (W.frame(W.TEXT, b"hi"), W.PROTOCOL_ERROR),                                        # unmasked
    (bytes([0x81 | 0x40, 0x80]) + MASK, W.PROTOCOL_ERROR),                             # RSV1
    (W.frame(W.PING, b"x" * 126, mask=MASK), W.PROTOCOL_ERROR),
    (W.frame(W.PING, b"x", fin=False, mask=MASK), W.PROTOCOL_ERROR),
    (W.frame(W.CONT, b"x", mask=MASK), W.PROTOCOL_ERROR),
    (W.frame(W.TEXT, b"a", fin=False, mask=MASK) + W.frame(W.TEXT, b"b", mask=MASK), W.PROTOCOL_ERROR),
    (W.frame(0x3, b"", mask=MASK), W.PROTOCOL_ERROR),
    (W.frame(0xB, b"", mask=MASK), W.PROTOCOL_ERROR),
    (W.frame(W.TEXT, b"\xff", mask=MASK), W.BAD_DATA),
    (W.frame(W.TEXT, b"\xce", fin=False, mask=MASK) + W.frame(W.CONT, b"\x41", mask=MASK), W.BAD_DATA),
    (W.frame(W.CLOSE, b"\x03\xe8\xff", mask=MASK), W.BAD_DATA),
    (W.frame(W.CLOSE, b"\x03", mask=MASK), W.PROTOCOL_ERROR),
    (W.frame(W.CLOSE, (999).to_bytes(2, "big"), mask=MASK), W.PROTOCOL_ERROR),
    (W.frame(W.CLOSE, (1006).to_bytes(2, "big"), mask=MASK), W.PROTOCOL_ERROR),
    (bytes([0x82, 0xFE, 0x00, 0x05]) + MASK + b"12345", W.PROTOCOL_ERROR),            # non-minimal length
    (bytes([0x82, 0xFF]) + (1 << 63).to_bytes(8, "big") + MASK, W.PROTOCOL_ERROR),
])
def test_protocol_errors(wire, code):
    with pytest.raises(W.ProtocolError) as err:
        W.Parser().feed(wire)
    assert err.value.code == code


def test_message_size_is_checked_before_buffering():
    p = W.Parser(max_message=100)
    with pytest.raises(W.ProtocolError) as err:
        p.feed(bytes([0x82, 0xFF]) + (10 ** 9).to_bytes(8, "big"))        # header only: 1 GB announced
    assert err.value.code == W.TOO_BIG
    p = W.Parser(max_message=100)
    with pytest.raises(W.ProtocolError):
        p.feed(W.frame(W.BINARY, b"x" * 60, fin=False, mask=MASK) + W.frame(W.CONT, b"x" * 60, mask=MASK))


def test_close_codes():
    assert W.Parser().feed(W.frame(W.CLOSE, b"", mask=MASK))[0].code == 1005
    ev = W.Parser().feed(W.frame(W.CLOSE, (4001).to_bytes(2, "big") + b"bye", mask=MASK))[0]
    assert (ev.code, ev.reason) == (4001, "bye")


@settings(max_examples=1500, deadline=None)
@given(st.binary(max_size=200))
def test_websocket_fuzz_only_raises_protocol_errors(blob):
    try:
        W.Parser().feed(blob)
    except W.ProtocolError:
        pass


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=300), st.integers(1, 9))
def test_text_round_trips_in_any_fragmentation(text, parts):
    raw = text.encode()
    size = max(1, len(raw) // parts)
    pieces = [raw[i:i + size] for i in range(0, len(raw), size)] or [b""]
    wire = b"".join(W.frame(W.TEXT if i == 0 else W.CONT, piece, fin=i == len(pieces) - 1, mask=MASK)
                    for i, piece in enumerate(pieces))
    assert [e.data for e in W.Parser().feed(wire)] == [text]


def test_handshake_checks():
    good = {"upgrade": "websocket", "connection": "keep-alive, Upgrade", "sec-websocket-version": "13",
            "sec-websocket-key": base64.b64encode(b"x" * 16).decode()}
    assert W.handshake_problem("GET", good) is None
    assert W.handshake_problem("POST", good)
    assert W.handshake_problem("GET", dict(good, **{"sec-websocket-version": "8"}))
    assert W.handshake_problem("GET", dict(good, **{"sec-websocket-key": "short"}))
    assert W.handshake_problem("GET", dict(good, connection="close"))


# ------------------------------------------------------------ the server

APP = '''
from pyweb import App, server, channel, publish, session

app = App(title="Net")

@server
def shout(text: str) -> str:
    publish("room", {"text": text})
    return text.upper()

@server
def sign_in(name: str) -> str:
    session.login(name)
    return name

@app.page("/")
def Home():
    feed = channel("room")
    <p id="feed">{feed}</p>
'''


@pytest.fixture
def running(tmp_path, monkeypatch):
    from pyweb.hosting import Site
    from pyweb.net.server import Running
    monkeypatch.delenv("PYWEB_ENV", raising=False)
    (tmp_path / "app.pyweb").write_text(APP)
    r = Running(Site(str(tmp_path / "app.pyweb"), debug=True, rate_limit=False))
    yield r
    r.stop(timeout=1)


def raw(r, data, *, wait=0.4, timeout=3):
    s = socket.create_connection(("127.0.0.1", r.port), timeout=timeout)
    s.sendall(data)
    time.sleep(wait)
    out = b""
    try:
        while True:
            part = s.recv(65536)
            if not part:
                break
            out += part
    except socket.timeout:
        pass
    s.close()
    return out


def test_keep_alive_and_pipelining(running):
    out = raw(running, b"GET /healthz HTTP/1.1\r\nHost: a\r\n\r\nHEAD / HTTP/1.1\r\nHost: a\r\n\r\n"
                       b"GET /healthz HTTP/1.1\r\nHost: a\r\nConnection: close\r\n\r\n")
    assert out.count(b"HTTP/1.1 200 OK") == 3 and out.endswith(b"}")


def test_chunked_post_and_100_continue(running):
    body = json.dumps({"args": {"text": "hey"}}).encode()
    wire = (b"POST /__pyweb/rpc/shout HTTP/1.1\r\nHost: a\r\nContent-Type: application/json\r\n"
            b"Transfer-Encoding: chunked\r\nExpect: 100-continue\r\nConnection: close\r\n\r\n" +
            b"%x\r\n%s\r\n0\r\n\r\n" % (len(body), body))
    out = raw(running, wire)
    assert out.startswith(b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 200") and out.endswith(b'"HEY"}')


@pytest.mark.parametrize("wire,status", [
    (b"POST / HTTP/1.1\r\nHost: a\r\nContent-Length: 4\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n", b"400"),
    (b"GET / HTTP/1.1\r\nHost: a\r\nX: " + b"a" * 20000 + b"\r\n\r\n", b"431"),
    (b"POST /__pyweb/rpc/shout HTTP/1.1\r\nHost: a\r\nContent-Length: 99999999\r\nExpect: 100-continue\r\n\r\n",
     b"413"),
])
def test_bad_requests_get_an_answer_and_a_closed_connection(running, wire, status):
    out = raw(running, wire + b"GET /healthz HTTP/1.1\r\nHost: a\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 " + status) and out.count(b"HTTP/1.1") == 1     # nothing smuggled after
    assert b"100 Continue" not in out


def test_slow_request_heads_are_dropped(running, monkeypatch):
    from pyweb.net import server as S
    monkeypatch.setattr(S, "HEAD_TIMEOUT", 0.3)
    s = socket.create_connection(("127.0.0.1", running.port), timeout=3)
    s.sendall(b"GET / HTTP/1.1\r\nHo")
    time.sleep(0.8)
    assert s.recv(1000).startswith(b"HTTP/1.1 408")


def test_connection_cap(running):
    running.server.max_connections = 1
    hold = socket.create_connection(("127.0.0.1", running.port))
    time.sleep(0.2)
    try:
        assert raw(running, b"GET /healthz HTTP/1.1\r\nHost: a\r\n\r\n").startswith(b"HTTP/1.1 503")
    finally:
        hold.close()


def test_event_stream_is_chunked(running):
    html = urllib.request.urlopen(running.url).read().decode()
    feed = re.search(r'id="feed">([^<]+)<', html).group(1)
    s = socket.create_connection(("127.0.0.1", running.port), timeout=3)
    s.sendall(f"GET /__pyweb/events?feed={feed} HTTP/1.1\r\nHost: a\r\n\r\n".encode())
    time.sleep(0.3)
    urllib.request.urlopen(urllib.request.Request(running.url + "/__pyweb/rpc/shout",
                                                  data=b'{"args": {"text": "x"}}',
                                                  headers={"Content-Type": "application/json"}))
    time.sleep(0.5)
    out = s.recv(65536)
    s.close()
    assert b"Transfer-Encoding: chunked" in out and b'data: {"text":"x"}' in out


# ------------------------------------------------------------- live socket

def feed_of(r, cookie=""):
    req = urllib.request.Request(r.url, headers={"Cookie": cookie} if cookie else {})
    return re.search(r'id="feed">([^<]+)<', urllib.request.urlopen(req).read().decode()).group(1)


def shout(r, text):
    urllib.request.urlopen(urllib.request.Request(r.url + "/__pyweb/rpc/shout",
                                                  data=json.dumps({"args": {"text": text}}).encode(),
                                                  headers={"Content-Type": "application/json"}))


def test_socket_subscribes_and_receives(running):
    c = Client(running)
    assert c.status == 101 and c.next("hello")["v"] == 2
    feed = feed_of(running)
    c.send({"t": "sub", "s": 1, "feed": feed})
    time.sleep(0.2)
    shout(running, "one")
    shout(running, "two")
    assert c.next("m")["d"] == {"text": "one"}
    second = c.next("m")
    assert second["d"] == {"text": "two"}
    c.send({"t": "ping"})
    assert c.next("pong")
    c.raw(W.frame(W.PING, b"abc", mask=MASK))
    c.send({"t": "unsub", "s": 1})
    time.sleep(0.2)
    shout(running, "three")
    with pytest.raises(AssertionError):
        c.next("m", timeout=0.6)
    # a new subscription resumes after the last message it saw
    c.send({"t": "sub", "s": 2, "feed": feed, "since": second["i"]})
    assert c.next("m")["d"] == {"text": "three"}


def test_cross_site_sockets_are_refused(running):
    assert Client(running, origin="https://evil.example").status == 403
    assert Client(running, origin=False).status == 403


def test_allowed_origins_setting(running, monkeypatch):
    monkeypatch.setenv("PYWEB_ALLOWED_ORIGINS", "https://app.example.com")
    assert Client(running, origin="https://app.example.com").status == 101


def test_forged_feeds_and_bad_messages(running):
    c = Client(running)
    c.send({"t": "sub", "s": 1, "feed": "forged.token"})
    assert c.next("e")["status"] == 403
    c.send({"t": "sub", "s": "x", "feed": feed_of(running)})
    assert c.next("e")["status"] == 400
    c.raw(W.frame(W.TEXT, b"not json", mask=MASK))
    closed = c.next(W.Closed)
    assert isinstance(closed, W.Closed) and closed.code == 1008


def test_protocol_errors_close_the_socket(running):
    c = Client(running)
    c.next("hello")
    c.raw(W.frame(W.TEXT, b"unmasked"))
    closed = c.next(W.Closed)
    assert isinstance(closed, W.Closed) and closed.code == W.PROTOCOL_ERROR


def test_message_flood_is_cut_off(running):
    c = Client(running)
    c.next("hello")
    c.raw(b"".join(W.frame(W.TEXT, b'{"t":"ping"}', mask=MASK) for _ in range(300)))
    for _ in range(400):
        msg = c.next()
        if isinstance(msg, W.Closed) or msg is None:
            break
    assert isinstance(msg, W.Closed) and msg.code == 1008


def test_feeds_from_a_signed_in_page_belong_to_that_session(running):
    req = urllib.request.Request(running.url + "/__pyweb/rpc/sign_in", data=b'{"args": {"name": "ada"}}',
                                 headers={"Content-Type": "application/json"})
    cookie = urllib.request.urlopen(req).headers["Set-Cookie"].split(";")[0]
    feed = feed_of(running, cookie)
    stranger = Client(running)
    stranger.send({"t": "sub", "s": 1, "feed": feed})
    assert stranger.next("e")["status"] == 403
    owner = Client(running, cookie=cookie)
    owner.send({"t": "sub", "s": 1, "feed": feed})
    time.sleep(0.2)
    shout(running, "private")
    assert owner.next("m")["d"] == {"text": "private"}


def test_revoked_sessions_lose_their_socket(running, monkeypatch):
    from pyweb import auth
    from pyweb.net import live
    monkeypatch.setattr(live, "REVALIDATE", 0.3)
    monkeypatch.setattr(auth, "_versions", None)
    auth.use_session_versions(auth.SessionVersions())
    req = urllib.request.Request(running.url + "/__pyweb/rpc/sign_in", data=b'{"args": {"name": "bob"}}',
                                 headers={"Content-Type": "application/json"})
    cookie = urllib.request.urlopen(req).headers["Set-Cookie"].split(";")[0]
    c = Client(running, cookie=cookie)
    c.next("hello")
    auth.revoke_user("bob")
    closed = c.next(W.Closed, timeout=4)
    assert isinstance(closed, W.Closed) and closed.code == 4001


def test_draining_tells_sockets_to_reconnect(tmp_path, monkeypatch):
    from pyweb.hosting import Site
    from pyweb.net.server import Running
    (tmp_path / "app.pyweb").write_text(APP)
    r = Running(Site(str(tmp_path / "app.pyweb"), debug=True, rate_limit=False))
    c = Client(r)
    c.next("hello")
    r.stop(timeout=1)
    closed = c.next(W.Closed, timeout=4)
    assert isinstance(closed, W.Closed) and closed.code == 1001


# ---------------------------------------------------------------- ASGI

def test_asgi_websocket(tmp_path, monkeypatch):
    import asyncio

    from pyweb.asgi import create_app
    (tmp_path / "app.pyweb").write_text(APP)
    app = create_app(str(tmp_path / "app.pyweb"), debug=True, rate_limit=False)
    status, _, html = app.site.respond("GET", "/", {"Host": "t"})
    feed = re.search(rb'id="feed">([^<]+)<', html).group(1).decode()

    async def session(origin):
        inbox, sent = asyncio.Queue(), []
        await inbox.put({"type": "websocket.connect"})
        scope = {"type": "websocket", "path": "/__pyweb/ws", "query_string": b"", "client": ("1.2.3.4", 1),
                 "headers": [(b"host", b"t"), (b"origin", origin.encode())]}

        async def send(msg):
            sent.append(msg)

        task = asyncio.ensure_future(app(scope, inbox.get, send))
        await asyncio.sleep(0.3)
        return task, inbox, sent

    async def main():
        task, inbox, sent = await session("https://evil.example")
        await asyncio.wait_for(task, 3)
        assert sent == [{"type": "websocket.close", "code": 1008, "reason": sent[0]["reason"]}]

        task, inbox, sent = await session("http://t")
        assert sent[0] == {"type": "websocket.accept"}
        await inbox.put({"type": "websocket.receive", "text": json.dumps({"t": "sub", "s": 1, "feed": feed})})
        await asyncio.sleep(0.3)
        await asyncio.to_thread(app.site.respond, "POST", "/__pyweb/rpc/shout", {"Content-Type": "application/json"},
                                b'{"args": {"text": "asgi"}}')
        for _ in range(40):
            if any('"asgi"' in m.get("text", "") for m in sent):
                break
            await asyncio.sleep(0.05)
        assert any(json.loads(m["text"]).get("d") == {"text": "asgi"} for m in sent if "text" in m)
        await inbox.put({"type": "websocket.disconnect", "code": 1001})
        await asyncio.wait_for(task, 5)

    asyncio.run(main())


# --------------------------------------------------------- backpressure

class StubServer:
    def __init__(self, bus, name):
        self.bus, self.name = bus, name

    def in_request(self, req, fn, *args):
        return fn(*args)

    def open_feed(self, token, spec=None):
        return self.bus, self.name, 0


def test_a_lagging_live_query_gets_one_resync_instead_of_a_backlog():
    import asyncio

    from pyweb.net.live import LiveSession
    from pyweb.realtime import Bus
    bus = Bus()
    for i in range(30):
        bus.publish("pyweb.live:k", {"version": f"v{i}", "prev": f"v{i - 1}", "ops": []})
    live = LiveSession(StubServer(bus, "pyweb.live:k"), None, None)
    inbox, sent = asyncio.Queue(), []

    async def send(text):
        sent.append(json.loads(text))

    async def close(code, reason):
        sent.append({"closed": code})

    async def main():
        await inbox.put(json.dumps({"t": "sub", "s": 1, "feed": "x", "live": "spec"}))
        task = asyncio.ensure_future(live.run(inbox.get, send, close))
        await asyncio.sleep(0.3)
        await inbox.put(None)
        await asyncio.wait_for(task, 3)

    asyncio.run(main())
    kinds = [m.get("t") for m in sent]
    assert kinds.count("r") == 1 and "m" not in kinds


def test_a_client_that_stops_reading_is_disconnected(monkeypatch):
    import asyncio

    from pyweb.net import live as L
    from pyweb.realtime import Bus
    monkeypatch.setattr(L, "SEND_TIMEOUT", 0.2)
    bus = Bus()
    session = L.LiveSession(StubServer(bus, "room"), None, None)
    inbox, closed = asyncio.Queue(), []
    blocked = asyncio.Event()

    async def send(text):
        if '"t":"m"' in text:
            await blocked.wait()                       # the socket's buffers are full: never completes

    async def close(code, reason):
        closed.append(code)

    async def main():
        await inbox.put(json.dumps({"t": "sub", "s": 1, "feed": "x"}))
        task = asyncio.ensure_future(session.run(inbox.get, send, close))
        await asyncio.sleep(0.1)
        bus.publish("room", {"big": "x" * 1000})
        session.wake.set()
        await asyncio.wait_for(task, 3)

    asyncio.run(main())
    assert closed == [L.TOO_SLOW]


# ---------------------------------------------------------------- presence

def test_rooms_track_members_in_join_order_and_expire():
    from pyweb.rooms import TTL, Rooms
    rooms = Rooms()
    rooms.apply("r", {"op": "join", "id": "b", "info": {"name": "bob"}}, now=0)
    rooms.apply("r", {"op": "join", "id": "a", "info": {"name": "ada"}}, now=1)
    rooms.apply("r", {"op": "beat", "id": "b"}, now=TTL)
    assert [m["name"] for m in rooms.members("r", now=TTL)] == ["bob", "ada"]
    assert [m["id"] for m in rooms.members("r", now=TTL + 2)] == ["b"]          # ada's server stopped renewing
    rooms.apply("r", {"op": "leave", "id": "b"}, now=TTL + 3)
    assert rooms.members("r", now=TTL + 3) == [] and "r" not in rooms.rooms


def test_member_info_is_small_and_user_cant_be_faked():
    from pyweb.rooms import clean_info
    assert clean_info({"name": "x", "user": "admin", "id": "spoof"}, user="ada") == {"name": "x", "user": "ada"}
    assert clean_info("junk") == {"user": None}
    with pytest.raises(ValueError):
        clean_info({"blob": "x" * 2000})


def test_presence_over_the_socket(running, tmp_path):
    (tmp_path / "room.pyweb").write_text('''
from pyweb import App, channel, presence

app = App()

@app.page("/")
def Home():
    room = presence("lobby")
    feed = channel("room")
    <p id="room">{room}</p>
    <p id="feed">{feed}</p>
''')
    from pyweb.hosting import Site
    from pyweb.net.server import Running
    r = Running(Site(str(tmp_path / "room.pyweb"), debug=True, rate_limit=False))
    try:
        html = urllib.request.urlopen(r.url).read().decode()
        room = re.search(r'id="room">([^<]+)<', html).group(1)
        feed = re.search(r'id="feed">([^<]+)<', html).group(1)
        a, b = Client(r), Client(r)
        a.send({"t": "join", "s": 1, "feed": room, "info": {"name": "ada", "user": "root"}})
        [me] = a.next("m")["d"]["members"]
        assert me["name"] == "ada" and me["user"] is None and me["id"]        # "user" comes from the server
        b.send({"t": "join", "s": 7, "feed": room, "info": {"name": "bob"}})
        names = lambda c: [m["name"] for m in c.next("m")["d"]["members"]]   # noqa: E731
        assert wait_for(lambda: names(b) == ["ada", "bob"])
        assert wait_for(lambda: names(a) == ["ada", "bob"])
        b.send({"t": "cast", "s": 7, "d": {"x": 1}})
        msg = a.next("m")
        while "cast" not in msg["d"]:
            msg = a.next("m")
        assert msg["d"]["cast"] == {"x": 1}
        b.send({"t": "leave", "s": 7})
        assert wait_for(lambda: names(a) == ["ada"])
        c = Client(r)
        c.send({"t": "join", "s": 1, "feed": feed, "info": {}})            # an ordinary feed isn't a room
        assert c.next("e")["status"] == 403
    finally:
        r.stop(timeout=1)


def wait_for(check, tries=10):
    for _ in range(tries):
        try:
            if check():
                return True
        except AssertionError:
            pass
    return False


def test_a_request_pipelined_after_a_chunked_body(running):
    body = b'{"args": {"text": "a"}}'
    wire = (b"POST /__pyweb/rpc/shout HTTP/1.1\r\nHost: a\r\nContent-Type: application/json\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n" + b"%x\r\n%s\r\n0\r\n\r\n" % (len(body), body) +
            b"GET /healthz HTTP/1.1\r\nHost: a\r\nConnection: close\r\n\r\n")
    out = raw(running, wire)
    assert out.count(b"HTTP/1.1 200 OK") == 2 and b'"A"' in out
