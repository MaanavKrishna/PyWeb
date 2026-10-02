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
