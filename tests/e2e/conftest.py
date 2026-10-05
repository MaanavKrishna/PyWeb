"""Real-browser tests (Playwright + Chromium). Skipped when unavailable.

Run: pip install playwright && playwright install chromium
"""

import pytest

pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - browser binary missing
            pytest.skip(f"chromium not available: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    pg = ctx.new_page()
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
