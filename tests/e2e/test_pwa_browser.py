"""Installable apps in a real browser: the manifest is found, the service worker registers under the
strict CSP (Trusted Types), and pages fall back to the offline page when the server is gone."""

import textwrap

import pytest
from playwright.sync_api import expect

from pyweb.testing import serve
from tests.e2e.conftest import ready, until

pytestmark = pytest.mark.chromium_only   # service worker and install checks use Chromium

APP = textwrap.dedent('''
    from pyweb import App
    from pyweb.pwa import PWA

    app = App(title="Field Notes", pwa=PWA(theme_color="#0f766e", cache_pages=True))


    @app.page("/")
    def Home():
        n = 0

        def inc():
            n += 1

        <section>
            <h1>Field notes</h1>
            <button id="inc" onclick={inc}>n = {n}</button>
            <button id="push" data-pw-push="subscribe">Notify me</button>
            <a href="/two">two</a>
        </section>


    @app.page("/two")
    def Two():
        <h1>Page two</h1>
''')


def test_installable_and_offline(page, tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(APP)
    with serve(str(path)) as url:
        response = page.goto(url)
        assert "pyweb-sw" in response.headers["content-security-policy"]
        ready(page)
        manifest = page.evaluate("fetch('/manifest.webmanifest').then(r => r.json())")
        assert manifest["name"] == "Field Notes" and manifest["theme_color"] == "#0f766e"
        # Registered through the pyweb-sw policy (a plain string would be a Trusted Types violation).
        until(page, "navigator.serviceWorker.controller !== null", timeout=10)
        assert page.evaluate("navigator.serviceWorker.controller.scriptURL").endswith("/sw.js")
        page.click("#inc")
        expect(page.locator("#inc")).to_have_text("n = 1")              # the app still works under the worker
        until(page, "document.documentElement.dataset.pwPush !== undefined")
        assert page.evaluate("document.documentElement.dataset.pwPush") in ("default", "denied", "unsupported")
        assert "pyweb_display=browser" in page.evaluate("document.cookie")
        page.goto(url + "/two")                                          # kept for offline (signed out)
        expect(page.locator("h1")).to_have_text("Page two")
    # The server is gone: a kept page still opens, others show the offline page.
    page.goto(url + "/two")
    expect(page.locator("h1")).to_have_text("Page two")
    page.goto(url + "/never-visited")
    expect(page.locator("h1")).to_have_text("You're offline")
    assert "Field Notes" in page.content()
