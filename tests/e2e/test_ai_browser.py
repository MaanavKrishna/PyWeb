"""Streaming, cancelling and Markdown in a real browser, and the ai-chat example."""

import json
import re
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect

from pyweb.markdown import render
from pyweb.testing import serve
from tests.e2e.conftest import ready
from tests.e2e.test_runtime_dom import open_runtime, run
from tests.test_markdown import ATTACKS, CASES

CHAT = str(Path(__file__).parent.parent.parent / "examples" / "ai-chat" / "app.pyweb")


def test_browser_markdown_matches_the_server(page):
    open_runtime(page)
    texts = [text for text, _html in CASES + ATTACKS]
    browser = run(page, f"""const M = await import("http://pw.test/markdown.js");
        return {json.dumps(texts)}.map((t) => M.renderMarkdown(t));""")
    assert browser == [render(t) for t in texts]


SLOW = '''import time

from pyweb import App, server

app = App()
LOG = []


@server
def count(n: int):
    try:
        for i in range(n):
            time.sleep(0.1)
            yield i
    finally:
        LOG.append("closed")


@server
def log() -> list:
    return LOG


@app.page("/")
def Home():
    got = []
    status = ""
    stream = None

    async def go():
        status = "running"
        stream = count(50)
        async for i in stream:
            got.append(i)
        status = "stopped" if len(got) < 50 else "done"

    def stop():
        stream.cancel()

    <button id="go" onclick={go}>go</button>
    <button id="stop" onclick={stop}>stop</button>
    <p id="got">{len(got)}</p>
    <p id="status">{status}</p>
'''


def test_values_arrive_while_streaming_and_cancel_stops_the_server(page, tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(SLOW)
    with serve(str(path)) as url:
        page.goto(url)
        ready(page)
        page.click("#go")
        expect(page.locator("#got")).to_have_text(re.compile(r"^([3-9]|[1-4]\d)$"))   # some values, while...
        expect(page.locator("#status")).to_have_text("running")                      # ...the call still runs
        page.click("#stop")
        expect(page.locator("#status")).to_have_text("stopped")
        stopped_at = int(page.inner_text("#got"))
        time.sleep(0.4)
        assert int(page.inner_text("#got")) == stopped_at < 50
        req = urllib.request.Request(url + "/__pyweb/rpc/log", data=b'{"args":{}}',
                                     headers={"Content-Type": "application/json"})
        assert json.load(urllib.request.urlopen(req))["result"] == ["closed"]   # the generator was closed


def test_ai_chat_streams_markdown_and_stops(page, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    with serve(CHAT) as url:
        page.goto(url)
        ready(page)
        page.fill("#prompt", "What is PyWeb?")
        page.click("#send")
        expect(page.locator(".bubble.user")).to_have_text("What is PyWeb?")
        expect(page.locator("#stop")).to_be_visible()
        answer = page.locator(".bubble.assistant .markdown")
        expect(answer.locator("strong").first).to_have_text("What is PyWeb?")   # Markdown as it streams
        expect(page.locator("#send")).to_be_visible(timeout=15000)
        expect(answer.locator("li")).to_have_count(3)
        assert page.input_value("#prompt") == ""

        page.fill("#prompt", "Again")
        page.click("#send")
        expect(page.locator(".bubble.assistant").nth(1)).to_contain_text("You asked")
        page.click("#stop")
        expect(page.locator("#send")).to_be_visible()
        partial = page.inner_text(".bubble.assistant >> nth=1")
        time.sleep(0.5)
        assert page.inner_text(".bubble.assistant >> nth=1") == partial and "restart" not in partial
