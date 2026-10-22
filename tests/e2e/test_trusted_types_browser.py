"""Trusted Types in a real browser: PyWeb's pages enforce them, its own runtime (Markdown, client
navigation, hydration) works under them, and violations are reported to the server."""

import textwrap

from playwright.sync_api import expect

from pyweb.telemetry.instruments import csp_violations
from pyweb.testing import serve
from tests.e2e.conftest import ready

APP = textwrap.dedent('''
    from pyweb import App, Markdown

    app = App(title="Notes")


    @app.layout
    def Shell(children):
        <div>
            <nav><a href="/">One</a> <a href="/two">Two</a></nav>
            <main>{children}</main>
        </div>


    @app.page("/")
    def One():
        text = "**bold** and <script>alert(1)</script>"

        def change():
            text = "# Heading\\n\\n- item *one*"

        <section>
            <button id="change" onclick={change}>change</button>
            <Markdown text={text} />
        </section>


    @app.page("/two")
    def Two():
        n = 41

        def inc():
            n += 1

        <section>
            <button id="inc" onclick={inc}>n = {n}</button>
            <Markdown text={"_two_"} />
        </section>
''')


def _app(tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(APP)
    return str(path)


def _total():
    return sum(csp_violations.series.values())


def test_pages_enforce_trusted_types_and_the_runtime_works_under_them(page, tmp_path):
    with serve(_app(tmp_path)) as url:
        response = page.goto(url)
        csp = response.headers["content-security-policy"]
        assert "require-trusted-types-for 'script'" in csp and "trusted-types pyweb-md pyweb-nav" in csp
        ready(page)
        expect(page.locator(".markdown strong")).to_have_text("bold")
        assert page.locator(".markdown script").count() == 0           # escaped, never markup
        page.click("#change")                                            # Markdown re-renders in the browser
        expect(page.locator(".markdown h1")).to_have_text("Heading")
        expect(page.locator(".markdown em")).to_have_text("one")

        page.evaluate("window.loads = 1")
        page.click("text=Two")                                           # client navigation parses HTML
        expect(page.locator("#inc")).to_have_text("n = 41")              # state handed over, page hydrated
        assert page.evaluate("window.loads") == 1
        page.click("#inc")
        expect(page.locator("#inc")).to_have_text("n = 42")
        expect(page.locator(".markdown em")).to_have_text("two")
        page.go_back()
        expect(page.locator(".markdown strong")).to_have_text("bold")
        assert page.errors == []


def test_strings_in_html_sinks_are_refused_and_reported(page, tmp_path):
    before = _total()
    with serve(_app(tmp_path)) as url:
        page.goto(url)
        ready(page)
        # What a bug passing visitor text to innerHTML would do: the browser refuses it.
        outcome = page.evaluate("""() => {
            try { document.body.insertAdjacentHTML("beforeend", "<img src=x onerror=alert(1)>"); return "ran"; }
            catch (e) { return e.name; } }""")
        assert outcome == "TypeError"
        assert page.locator("img[src=x]").count() == 0
        import time
        end = time.monotonic() + 20
        while _total() == before and time.monotonic() < end:     # the browser posts a report
            time.sleep(0.1)
        assert _total() > before
    assert any("TrustedHTML" in e for e in page.errors)
    page.errors.clear()                                          # the refusal is logged; that's expected
