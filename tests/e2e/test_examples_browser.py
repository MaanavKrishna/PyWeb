"""Every example app, driven in a real browser under the production CSP."""

from playwright.sync_api import expect

from pyweb.testing import serve
from tests.e2e.conftest import ready


def test_counter(page):
    with serve("examples/counter/app.pyweb") as url:
        page.goto(url)
        ready(page)
        expect(page.locator("#reset")).to_be_disabled()
        page.click("#inc")
        page.click("#inc")
        expect(page.locator("#inc")).to_have_text("Count: 2")
        page.fill("#step", "5")
        page.click("#inc")
        expect(page.locator("#inc")).to_have_text("Count: 7")
        expect(page.locator("#doubled")).to_have_text("Doubled: 14")
        page.click("#reset")
        expect(page.locator("#inc")).to_have_text("Count: 0")


def test_todo(page):
    with serve("examples/todo/app.pyweb") as url:
        page.goto(url)
        ready(page)
        expect(page.locator(".empty")).to_be_visible()
        expect(page.locator("#add")).to_be_disabled()
        for title in ["write docs", "ship 1.0", "celebrate"]:
            page.fill("#new", title)
            page.press("#new", "Enter")
        expect(page.locator("#list li")).to_have_count(3)
        expect(page.locator("#left")).to_have_text("3 items left")
        page.locator("#list li input[type=checkbox]").nth(1).check()
        expect(page.locator("#left")).to_have_text("2 items left")
        expect(page.locator("#list li.done")).to_have_text(["ship 1.0×"])
        page.click("text=active")
        expect(page.locator("#list li span")).to_have_text(["write docs", "celebrate"])
        page.click("text=done")
        expect(page.locator("#list li span")).to_have_text(["ship 1.0"])
        page.click("text=all")
        page.click("#clear")
        expect(page.locator("#list li span")).to_have_text(["write docs", "celebrate"])
        page.locator("#list li .remove").first.click()
        expect(page.locator("#left")).to_have_text("1 item left")


def test_blog(page, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "blog.db"))
    with serve("examples/blog/app.pyweb") as url:
        page.goto(url)
        ready(page)
        expect(page.locator(".empty")).to_be_visible()
        page.click("#publish")
        expect(page.locator("#error")).to_have_text("A post needs a title and a body.")
        page.fill("#title", "Hello <world>")
        page.fill("#body", "First post body")
        page.click("#publish")
        expect(page.locator("article h2")).to_have_text(["Hello <world>"])
        expect(page.locator("#title")).to_have_value("")
        page.click("article h2 a")
        expect(page.locator("h1")).to_have_text("Hello <world>")
        resp = page.goto(url + "/posts/999")
        assert resp.status == 404
        resp = page.goto(url + "/posts/not-a-number")
        assert resp.status == 404


def test_auth(page):
    with serve("examples/auth/app.pyweb") as url:
        page.goto(url + "/account")
        assert page.url == url + "/"
        ready(page)
        page.click("text=Create one")
        ready(page)
        page.fill("#name", "Ada")
        page.fill("#email", "ada@example.com")
        page.fill("#password", "short")
        page.click("#register")
        expect(page.locator("#error")).to_contain_text("8+ characters")
        page.fill("#password", "correct horse")
        page.click("#register")
        page.wait_for_url(url + "/account")
        expect(page.locator("h1")).to_have_text("Hello, Ada")
        page.click("#logout")
        page.wait_for_url(url + "/")
        ready(page)
        page.fill("#email", "ada@example.com")
        page.fill("#password", "wrong password")
        page.click("#login")
        expect(page.locator("#error")).to_have_text("Wrong email or password.")
        page.fill("#password", "correct horse")
        page.click("#login")
        page.wait_for_url(url + "/account")
        page.goto(url + "/")
        assert page.url == url + "/account"


def test_chat_two_clients(browser):
    with serve("examples/chat/app.pyweb") as url:
        a = browser.new_context().new_page()
        b = browser.new_context().new_page()
        urls, sockets = [], []
        b.on("request", lambda r: urls.append(r.url))
        b.on("websocket", lambda ws: sockets.append(ws.url))
        for p in (a, b):
            p.goto(url + "/chat/python")
            ready(p)
        a.fill("#author", "ada")
        a.fill("#draft", "hello from A")
        a.press("#draft", "Enter")
        expect(a.locator(".msg")).to_have_text(["adahello from A"])
        # Pushed over the page's WebSocket: no event streams or polling involved.
        expect(b.locator(".msg")).to_have_text(["adahello from A"], timeout=2000)
        assert len(sockets) == 1 and sockets[0].endswith("/__pyweb/ws")
        assert not any("/__pyweb/events" in u or "/__pyweb/poll" in u or "/rpc/history" in u for u in urls)
        assert b.goto(url + "/chat/nope").status == 404


def test_chat_uses_event_streams_without_websockets(browser):
    with serve("examples/chat/app.pyweb") as url:
        a = browser.new_context().new_page()
        b = browser.new_context().new_page()
        b.add_init_script("delete window.WebSocket")
        urls = []
        b.on("request", lambda r: urls.append(r.url))
        for p in (a, b):
            p.goto(url + "/chat/random")
            ready(p)
        a.fill("#draft", "via a stream")
        a.press("#draft", "Enter")
        expect(b.locator(".msg")).to_have_text(["guestvia a stream"], timeout=4000)
        assert any("/__pyweb/events?feed=" in u for u in urls)


def test_chat_falls_back_to_polling_without_sse(browser):
    with serve("examples/chat/app.pyweb") as url:
        a = browser.new_context().new_page()
        b = browser.new_context().new_page()
        b.add_init_script("delete window.WebSocket")
        b.route("**/__pyweb/events*", lambda route: route.fulfill(status=503, body="no streams here"))
        for p in (a, b):
            p.goto(url + "/chat/random")
            ready(p)
        a.fill("#draft", "via polling")
        a.press("#draft", "Enter")
        expect(b.locator(".msg")).to_have_text(["guestvia polling"], timeout=6000)


def test_showcase_server_search(page):
    with serve("examples/showcase/app.pyweb") as url:
        page.goto(url)
        ready(page)
        expect(page.locator("#results li")).to_have_count(5)
        page.fill("#q", "mo")
        expect(page.locator("#results li")).to_have_count(2)
        page.fill("#qty", "3")
        expect(page.locator("#total")).to_have_text("Total: $300")
        page.click("#inc")
        expect(page.locator("#inc")).to_have_text("Count: 1")


def test_mcp_screenshot_sees_the_page_and_runs_steps(tmp_path, monkeypatch):
    import base64

    from pyweb import mcp
    monkeypatch.chdir(tmp_path)
    server = mcp.Server()
    mcp.scaffold(str(tmp_path / "app"), "counter")
    res = server.call_tool("pyweb_screenshot", {"path": "app/app.pyweb", "steps": [
        {"action": "click", "selector": "#inc"}, {"action": "click", "selector": "#inc"}]})
    assert res["isError"] is False
    info = res["structuredContent"]
    assert info["status"] == 200 and info["hydration"] == "hydrated"
    assert "Count: 2" in info["text"] and info["console"] == []
    image = next(c for c in res["content"] if c["type"] == "image")
    assert base64.b64decode(image["data"]).startswith(b"\x89PNG")
    bad = server.call_tool("pyweb_screenshot", {"path": "app/app.pyweb", "steps": [
        {"action": "click", "selector": "#missing"}]})["structuredContent"]
    assert bad["steps"][0]["ok"] is False and "#missing" in bad["steps"][0]["error"]
