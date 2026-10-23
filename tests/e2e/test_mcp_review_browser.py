"""pyweb_review and pyweb_screenshot from the MCP server, in a real browser."""

import textwrap

import pytest

from pyweb import mcp

pytestmark = pytest.mark.chromium_only     # the MCP tools drive Chromium

APP = textwrap.dedent('''
    from pyweb import App
    from pyweb.ui import Button

    app = App(title="Review", database="sqlite:///app.db")
    auth = app.use_auth()


    @app.page("/")
    def Home():
        n = 0

        def inc():
            n += 1

        <main>
            <h1>Hello</h1>
            <img src="/x.png" />
            <Button id="inc" onclick={inc}>Count {n}</Button>
        </main>


    @app.page("/me", login=True)
    def Me():
        <main><h1 id="who">{auth.user().email}</h1></main>
''')


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYWEB_WORKER", "0")
    monkeypatch.setattr(mcp, "APPS", mcp._Apps())
    (tmp_path / "app.pyweb").write_text(APP)
    return mcp.Server()


def test_review_screenshots_each_view_and_audits(server):
    res = server.call_tool("pyweb_review", {"schemes": ["light", "dark"], "widths": [900]})
    assert res["isError"] is False, res["content"][0]["text"]
    out = res["structuredContent"]
    images = [c for c in res["content"] if c["type"] == "image"]
    assert [(v["scheme"], v["width"], v["status"]) for v in out["views"]] == [("light", 900, 200), ("dark", 900, 200)]
    assert len(images) == 2
    assert any(f["kind"] == "a11y-img-alt" for f in out["static"])          # the source lint ran too
    if "violations" in out["views"][0]:                                      # axe-core reachable
        assert "image-alt" in out["serious"] + [v["id"] for v in out["views"][0]["violations"]]


def test_screenshot_signed_in(server):
    res = server.call_tool("pyweb_screenshot", {"url": "/me", "as_user": "ann@example.com"})
    assert res["isError"] is False, res["content"][0]["text"]
    assert "ann@example.com" in res["structuredContent"]["text"]
    assert res["structuredContent"]["target"] == "fresh"
