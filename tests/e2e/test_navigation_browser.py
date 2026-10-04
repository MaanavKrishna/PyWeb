"""Client-side navigation in a real browser: layouts keep their state, pages swap, history works."""

from pathlib import Path

from playwright.sync_api import expect

from pyweb.testing import serve
from tests.e2e.conftest import ready
from tests.test_multipage import APP


def app_file(tmp_path, source=APP):
    path = tmp_path / "app.pyweb"
    path.write_text(source)
    return str(path)


def documents(page):
    """Full page loads so far (navigation keeps `window.loads` alive; a reload resets it)."""
    return page.evaluate("window.loads || 0")


def test_links_swap_pages_and_keep_the_layout(page, tmp_path):
    requests = []
    with serve(app_file(tmp_path)) as url:
        page.on("request", lambda r: requests.append((r.resource_type, r.url[len(url):])))
        page.goto(url)
        ready(page)
        expect(page.locator("[data-pw-root=Shell]")).to_have_attribute("data-pw-mode", "hydrated")
        expect(page.locator("[data-pw-root=Home]")).to_have_attribute("data-pw-mode", "hydrated")
        page.evaluate("window.loads = 1")
        page.click("#menu")
        page.click("#inc")
        expect(page.locator("#menu")).to_have_text("close")
        requests.clear()

        page.click("text=Products")
        expect(page.locator("#info")).to_have_text("|1|[]|False")
        assert page.url == url + "/products" and documents(page) == 1
        assert [r for r in requests if r[0] == "document"] == []
        expect(page.locator("#menu")).to_have_text("close")                # layout state survived
        expect(page.locator("nav a[aria-current=page]")).to_have_text("Products")
        expect(page.locator("meta[name=description]")).to_have_attribute("content", "All products")

        page.click("text=Lamp")
        expect(page.locator("#name")).to_have_text("Lamp")
        assert page.title() == "Lamp - Shop"
        expect(page.locator("nav a[aria-current=true]")).to_have_text("Products")
        expect(page.locator("link[rel=canonical]")).to_have_attribute("href", "https://shop.test/products/1")

        page.go_back()
        expect(page.locator("#info")).to_be_visible()
        page.go_back()
        expect(page.locator("#inc")).to_have_text("0")                     # the page itself starts fresh
        page.click("#inc")
        expect(page.locator("#inc")).to_have_text("1")                     # and is interactive again
        page.go_forward()
        expect(page.locator("#info")).to_be_visible()
        assert documents(page) == 1

        page.click("text=Users")                                           # adds the static Admin layout
        expect(page.locator("section.admin #users")).to_have_text("users")
        expect(page.locator("#menu")).to_have_text("close")
        page.click("text=Broken")                                          # a .pyweb 404 page
        expect(page.locator("#missing")).to_have_text("404: nothing at /nope")
        page.click("text=Bare")                                            # no layout: everything swaps
        expect(page.locator("#bare")).to_have_text("bare")
        expect(page.locator("#menu")).to_have_count(0)
        assert documents(page) == 1


def test_hovering_a_link_prefetches_it(page, tmp_path):
    with serve(app_file(tmp_path)) as url:
        page.goto(url)
        ready(page)
        fetched = []
        page.on("request", lambda r: fetched.append(r.url[len(url):]) if r.resource_type == "fetch" else None)
        page.hover("text=Products")
        page.wait_for_function("performance.getEntriesByType('resource').some(e => e.name.endsWith('/products'))")
        page.click("text=Products")
        expect(page.locator("#info")).to_be_visible()
        assert fetched == ["/products"]                                    # the click reused the prefetch


SCROLLY = '''from pyweb import App
from pyweb.browser import navigate

app = App()


@app.page("/")
def Long():
    left = ""

    def on_unmount():
        localStorage.setItem("left", "Long")

    <div style="height: 3000px">top</div>
    <a id="next" href="/next">next</a>
    <a id="json" href="/healthz">health</a>


@app.page("/next")
def Next():
    def home():
        navigate("/?from=next")

    <p id="next-page">next</p>
    <button id="home" onclick={home}>home</button>
'''


def test_scroll_is_restored_and_unmount_runs(page, tmp_path):
    with serve(app_file(tmp_path, SCROLLY)) as url:
        page.goto(url)
        ready(page)
        page.evaluate("window.loads = 1; localStorage.clear()")
        page.click("#next")                                                # scrolls the link into view first
        expect(page.locator("#next-page")).to_be_visible()
        assert page.evaluate("scrollY") == 0
        assert page.evaluate("localStorage.getItem('left')") == "Long"
        page.go_back()
        expect(page.locator("#next")).to_be_visible()
        page.wait_for_function("scrollY > 2000")
        assert documents(page) == 1


def test_links_that_are_not_pages_load_normally(page, tmp_path):
    with serve(app_file(tmp_path, SCROLLY)) as url:
        page.goto(url)
        ready(page)
        page.evaluate("window.loads = 1")
        page.click("#json")                                                # JSON, not a page: a full load
        page.wait_for_url(url + "/healthz")
        assert '"ok"' in page.content() or "ok" in page.inner_text("body")
        assert documents(page) == 0


def test_navigate_from_browser_code(page, tmp_path):
    with serve(app_file(tmp_path, SCROLLY)) as url:
        page.goto(url + "/next")
        ready(page)
        page.evaluate("window.loads = 1")
        page.click("#home")
        page.wait_for_url(url + "/?from=next")
        expect(page.locator("#next")).to_be_visible()
        assert documents(page) == 1


WATCHER = '''from pyweb import App, channel, subscribe
from pyweb.browser import watch

app = App()


@app.page("/")
def Home():
    feed = channel("news")
    n = 0
    seen = []

    def on_mount():
        subscribe(feed, lambda msg: None)
        watch(lambda: n, changed)

    def changed(value):
        seen.append(value)

    def inc():
        n += 1

    <button id="inc" onclick={inc}>{n}</button>
    <p id="seen">{seen}</p>
    <a id="away" href="/away">away</a>


@app.page("/away")
def Away():
    m = 0

    def bump():
        m += 1

    <button id="bump" onclick={bump}>{m}</button>
'''

TRACK_SOURCES = """
const Real = window.EventSource;
window.__closed = 0;
window.EventSource = class extends Real { close() { window.__closed++; super.close(); } };
"""


def test_watch_and_on_mount_cleanup(page, tmp_path):
    page.add_init_script(TRACK_SOURCES)
    with serve(app_file(tmp_path, WATCHER)) as url:
        page.goto(url)
        ready(page)
        expect(page.locator("#seen")).to_have_text("[]")          # not called for the first value
        page.click("#inc")
        page.click("#inc")
        expect(page.locator("#seen")).to_have_text("[1, 2]")
        assert page.evaluate("window.__closed") == 0
        page.click("#away")
        expect(page.locator("#bump")).to_be_visible()
        assert page.evaluate("window.__closed") == 1               # the page's live feed was closed


def test_site_example(page):
    site = str(Path(__file__).parent.parent.parent / "examples" / "site" / "app.pyweb")
    with serve(site) as url:
        page.goto(url)
        ready(page)
        page.evaluate("window.loads = 1")
        page.click("#cart")
        page.click("text=low light")
        expect(page.locator("#plants li")).to_have_count(3)
        expect(page.locator(".site-nav a[aria-current=page]")).to_have_text("Plants")
        page.click("text=Pothos")
        expect(page.locator("h1")).to_have_text("Pothos")
        assert page.title() == "Pothos - Plant shop"
        expect(page.locator(".site-nav a[aria-current=true]")).to_have_text("Plants")
        expect(page.locator("#cart")).to_have_text("Cart: 1")            # the layout kept its state
        page.goto(url + "/plants/99")
        expect(page.locator("h1")).to_have_text("Not found")
        assert documents(page) == 0                                       # that one was a real load


CART = '''from pyweb import App, server

app = App()
ITEMS = []


@server
def add_item(name: str) -> int:
    ITEMS.append(name)
    return len(ITEMS)


@app.page("/")
def Home():
    count = 0

    async def add():
        count = await add_item("lamp")

    <button id="add" onclick={add}>Add ({count})</button>
    <a id="cart" href="/cart">Cart</a>


@app.page("/cart")
def Cart():
    items = list(ITEMS)
    clicks = 0

    def click():
        clicks += 1

    <p id="items" onclick={click}>{len(items)} items</p>
'''


def test_a_page_prefetched_before_a_server_call_is_fetched_again(page, tmp_path):
    with serve(app_file(tmp_path, CART)) as url:
        page.goto(url)
        ready(page)
        page.hover("#cart")                                                # prefetched with 0 items
        page.wait_for_function("performance.getEntriesByType('resource').some(e => e.name.endsWith('/cart'))")
        page.click("#add")
        expect(page.locator("#add")).to_have_text("Add (1)")
        page.click("#cart")
        expect(page.locator("#items")).to_have_text("1 items")             # not the stale prefetched copy
