"""Streaming server functions (`yield` on the server, `async for` in the browser) and the ai-chat app."""

import asyncio
import io
import json
import textwrap
from pathlib import Path

import pytest

from pyweb import RPCError
from pyweb.compiler import compile_source
from pyweb.compiler.errors import CompileError
from pyweb.testing import TestClient

APP = textwrap.dedent('''
    import asyncio

    from pyweb import App, RPCError, server, session

    app = App()
    LOG = []


    @server
    def count(n: int):
        try:
            for i in range(n):
                yield {"i": i}
        finally:
            LOG.append("closed")


    @server
    def fails():
        yield "first"
        raise RPCError("forbidden", "nope")


    @server
    def crashes():
        yield "first"
        raise ValueError("secret detail")


    @server
    def whoami():
        yield session.user()["sub"]


    @server
    async def ticks(n: int):
        for i in range(n):
            await asyncio.sleep(0)
            yield i


    @server
    def login() -> None:
        session.login(7)


    @server
    def log() -> list:
        return LOG


    @app.page("/")
    def Home():
        items = []
        stream = None

        async def go():
            stream = count(3)
            async for part in stream:
                items.append(part["i"])

        def stop():
            stream.cancel()

        <button onclick={go}>{len(items)}</button>
        <button onclick={stop}>stop</button>
''').lstrip()


@pytest.fixture
def client():
    return TestClient(source=APP)


def lines(resp):
    return [json.loads(line) for line in resp.text.splitlines()]


def test_yielded_values_arrive_as_ndjson_lines(client):
    resp = client.post("/__pyweb/rpc/count", {"args": {"n": 2}})
    assert resp.status == 200 and resp.header("Content-Type") == "application/x-ndjson"
    assert lines(resp) == [{"chunk": {"i": 0}}, {"chunk": {"i": 1}}, {"done": True}]
    assert client.rpc("count", n=3) == [{"i": 0}, {"i": 1}, {"i": 2}]
    assert client.rpc("log") == ["closed", "closed"]


def test_errors_part_way_arrive_in_band(client):
    assert lines(client.post("/__pyweb/rpc/fails", {"args": {}})) == [
        {"chunk": "first"}, {"error": {"code": "forbidden", "message": "nope", "details": {}}}]
    with pytest.raises(RPCError) as err:
        client.rpc("fails")
    assert err.value.code == "forbidden"
    crashed = lines(client.post("/__pyweb/rpc/crashes", {"args": {}}))
    assert crashed[-1]["error"]["code"] == "internal" and "secret" not in json.dumps(crashed)


def test_arguments_are_validated_before_streaming(client):
    resp = client.post("/__pyweb/rpc/count", {"args": {"n": "many"}})
    assert resp.status == 422 and resp.json()["error"]["code"] == "validation_error"


def test_the_generator_runs_in_the_request_context(client):
    client.rpc("login")
    assert client.rpc("whoami") == [7]


def test_async_generators_stream_too(client):
    assert client.rpc("ticks", n=3) == [0, 1, 2]


def test_closing_the_stream_closes_the_generator():
    from pyweb.runtime.server import RPCStream
    closed = []

    def gen():
        try:
            yield 1
            yield 2
        finally:
            closed.append(True)

    stream = RPCStream(gen(), "gen")
    first = next(iter(stream))
    assert json.loads(first) == {"chunk": 1}
    stream.close()
    assert closed == [True]


def test_asgi_streams_each_value_and_stops_on_disconnect(tmp_path):
    from pyweb.asgi import create_app
    path = tmp_path / "app.pyweb"
    path.write_text(APP.replace("for i in range(n):\n                yield {\"i\": i}",
                                "for i in range(n):\n                yield {\"i\": i}\n"
                                "                import time; time.sleep(0.05)"))
    app = create_app(str(path))
    body = json.dumps({"args": {"n": 1000}}).encode()
    sent, disconnect = [], asyncio.Event()

    async def receive():
        if not sent:
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)
        if sum(1 for m in sent if m.get("body")) >= 3:
            disconnect.set()                       # the browser cancelled after three values

    scope = {"type": "http", "method": "POST", "path": "/__pyweb/rpc/count", "query_string": b"",
             "headers": [(b"content-type", b"application/json"), (b"host", b"t")]}
    asyncio.run(asyncio.wait_for(app(scope, receive, send), 10))
    chunks = [json.loads(m["body"]) for m in sent if m.get("body")]
    assert chunks[:3] == [{"chunk": {"i": 0}}, {"chunk": {"i": 1}}, {"chunk": {"i": 2}}] and len(chunks) < 10
    assert app.site.app.module.LOG == ["closed"]          # the generator was closed


def test_browser_code_reads_streams_with_async_for():
    js = compile_source(APP)["pages"]["Home"]["js"]
    assert 'stream($rpc.stream("count", {"n": 3}));' in js
    assert "for await (part of $py.aiter(stream())) {" in js and "async function go()" in js
    assert "stream().cancel();" in js
    spec = next(s for s in compile_source(APP)["rpc"] if s["name"] == "count")
    assert spec["stream"] is True


def test_plain_for_over_a_stream_explains_async_for():
    src = APP.replace("async for part in stream:", "for part in count(3):")
    with pytest.raises(CompileError, match=r"read it with `async for \.\.\. in count\(\.\.\.\)`"):
        compile_source(src)


# ------------------------------------------------------------------ ai-chat

CHAT = Path(__file__).parent.parent / "examples" / "ai-chat" / "app.pyweb"


@pytest.fixture
def chat(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "AI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    return TestClient(str(CHAT))


def test_ai_chat_template_is_the_example():
    template = Path(__file__).parent.parent / "pyweb" / "templates" / "ai-chat.pyweb"
    assert template.read_text() == CHAT.read_text()


def test_demo_model_streams_markdown(chat, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    pieces = chat.rpc("reply", messages=[{"role": "user", "content": "Hello?"}])
    assert len(pieces) > 20 and "".join(pieces).startswith("You asked: **Hello?**")
    assert "Demo model" in chat.get("/").text


@pytest.mark.parametrize("messages, message", [
    ([], "the last message must be from the user"),
    ([{"role": "system", "content": "x"}], "{role, content} objects"),
    ([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}], "must be from the user"),
])
def test_bad_conversations_are_rejected(chat, messages, message):
    with pytest.raises(RPCError, match=message):
        chat.rpc("reply", messages=messages)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def fake_api(monkeypatch, lines_out):
    seen = {}

    def urlopen(req, timeout=None):
        seen["url"], seen["headers"], seen["body"] = req.full_url, dict(req.header_items()), json.loads(req.data)
        return FakeResponse("".join(line + "\n" for line in lines_out).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return seen


def test_anthropic_provider(chat, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    seen = fake_api(monkeypatch, [
        "event: message_start", 'data: {"type":"message_start"}', "",
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hel"}}',
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"lo"}}',
        'data: {"type":"message_stop"}'])
    assert chat.rpc("reply", messages=[{"role": "user", "content": "hi"}]) == ["Hel", "lo"]
    assert seen["url"] == "https://api.anthropic.com/v1/messages" and seen["headers"]["X-api-key"] == "sk-test"
    assert seen["body"]["model"] == "claude-sonnet-5-5" and seen["body"]["stream"] is True
    assert "sk-test" not in chat.get("/").text


def test_openai_compatible_provider(chat, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("AI_MODEL", "llama3")
    seen = fake_api(monkeypatch, ['data: {"choices":[{"delta":{"role":"assistant"}}]}',
                                  'data: {"choices":[{"delta":{"content":"Hi"}}]}',
                                  'data: {"choices":[{"delta":{"content":" there"}}]}', "data: [DONE]"])
    assert chat.rpc("reply", messages=[{"role": "user", "content": "yo"}]) == ["Hi", " there"]
    assert seen["url"] == "http://localhost:11434/v1/chat/completions" and seen["body"]["model"] == "llama3"
    assert seen["body"]["messages"][0]["role"] == "system" and "Authorization" not in seen["headers"]


def test_provider_errors_reach_the_browser_as_rpc_errors(chat, monkeypatch):
    import urllib.error
    monkeypatch.setenv("ANTHROPIC_API_KEY", "bad")

    def urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":"bad key"}'))

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(RPCError, match="answered 401") as err:
        chat.rpc("reply", messages=[{"role": "user", "content": "hi"}])
    assert err.value.code == "unavailable"
