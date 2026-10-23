"""Real-browser tests (Playwright). Skipped when unavailable.

Run: pip install playwright && playwright install chromium
Other engines: PYWEB_BROWSER=firefox (or webkit) after ``playwright install firefox``. Tests that
need a Chromium-only feature (CDP, Trusted Types, ...) are marked ``chromium_only`` and skipped there.
"""

import os

import pytest

pw = pytest.importorskip("playwright.sync_api")

ENGINE = os.environ.get("PYWEB_BROWSER", "chromium")


def pytest_configure(config):
    config.addinivalue_line("markers", "chromium_only: needs a Chromium-only browser feature")


def pytest_collection_modifyitems(config, items):
    if ENGINE == "chromium":
        return
    skip = pytest.mark.skip(reason=f"Chromium-only feature (running {ENGINE})")
    for item in items:
        if item.get_closest_marker("chromium_only"):
            item.add_marker(skip)


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = getattr(p, ENGINE).launch()
        except Exception as exc:  # noqa: BLE001 - browser binary missing
            pytest.skip(f"{ENGINE} not available: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.engine = ENGINE
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.errors = errors
    yield pg
    ctx.close()
    assert not [e for e in errors if "Failed to load resource" not in e], errors


def ready(pg):
    pg.wait_for_selector("[data-pw-ready]")


def until(pg, expression, timeout=5.0):
    """Wait for a JavaScript condition, polling from Python.

    page.wait_for_function re-evaluates its condition with eval(), which the
    pages' Content-Security-Policy (rightly) blocks, so it fails whenever the
    condition isn't already true on the first check.
    """
    import time
    end = time.monotonic() + timeout
    while not pg.evaluate(expression):
        if time.monotonic() > end:
            raise AssertionError(f"timed out waiting for {expression}")
        pg.wait_for_timeout(25)


def settle(pg, timeout=5.0):
    """Wait for the UI kit and motion modules that elements added late load on demand, so a test
    doesn't stop its server while the page is still fetching one."""
    plain = ":not([data-pw-ui=card]):not([data-pw-ui=alert]):not([data-pw-ui=table]):not([data-pw-ui=breadcrumb])"
    until(pg, f"!document.querySelector('[data-pw-ui]:not([data-pw-ui-ready]){plain}')", timeout)
    pg.wait_for_timeout(150)
