"""npm packages in a real browser: import map, CSP, classes, kwargs, ref and in-place mutation."""

from playwright.sync_api import expect

from pyweb import packages as P
from pyweb.testing import serve
from tests.e2e.conftest import ready
from tests.test_packages import registry

APP = '''from pyweb import App, npm

Gauge = npm("gauge-widget", "Gauge")
burst = npm("gauge-widget")

app = App()


@app.page("/")
def Home():
    box = None
    gauge = None
    shown = ""

    def on_mount():
        gauge = Gauge(box, max=10)

    def go():
        shown = burst(count=3)
        gauge.set(4)
        gauge.value = gauge.value + 1

    <main>
        <div id="box" ref={box}></div>
        <button id="go" onclick={go}>go</button>
        <p id="shown">{shown}</p>
        <p id="value">{gauge.value if gauge else "-"}</p>
    </main>
'''


def test_npm_package_runs_in_the_browser_under_csp(page, tmp_path):
    P.install(str(tmp_path), ["gauge-widget"], registry=registry())
    (tmp_path / "app.pyweb").write_text(APP)
    with serve(str(tmp_path / "app.pyweb")) as url:
        page.goto(url)
        ready(page)
        expect(page.locator("#value")).to_have_text("0")      # constructed with `new` in on_mount
        page.click("#go")
        expect(page.locator("#shown")).to_have_text('burst:{"count":3}')   # kwargs become an options object
        expect(page.locator("#box")).to_have_text("v=12")     # ref element + the dependency via the import map
        expect(page.locator("#value")).to_have_text("5")      # mutating the instance re-renders
        assert page.evaluate("document.querySelector('script[type=importmap]') !== null")
