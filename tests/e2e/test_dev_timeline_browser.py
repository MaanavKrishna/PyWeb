"""``pyweb dev``: the toolbar's Timeline tab records each change with its cause and steps back through them."""

import pytest

from tests.e2e.conftest import ready, until
from tests.e2e.test_dev_inspect_browser import dev as _dev  # noqa: F401 - the same app and dev server

dev = _dev


def tab(page):
    page.locator("pyweb-devtools .pill").click()
    page.locator('pyweb-devtools [data-tab="time"]').click()
    until(page, "!!document.querySelector('pyweb-devtools').shadowRoot.querySelector('[data-timeline-slider]')")


def slider(page, value):
    page.evaluate("""(v) => {
        const s = document.querySelector('pyweb-devtools').shadowRoot.querySelector('[data-timeline-slider]');
        s.value = String(v); s.dispatchEvent(new Event('input', {bubbles: true}));
    }""", value)


@pytest.fixture
def opened(page, dev):
    url, _folder, _log = dev
    page.goto(url)
    ready(page)
    return page


def test_clicks_are_steps_and_scrubbing_goes_back(opened):
    page = opened
    for _ in range(3):
        page.click("#b .counter")
    until(page, "document.querySelector('#b .counter').textContent === 'b=3'")
    steps = page.evaluate("window.__pywebTimeline.steps()")
    assert [s["n"] for s in steps] == [1, 2, 3]
    assert all(s["cause"].startswith("click button") for s in steps)
    tab(page)
    assert "3 changes in 3 steps" in page.locator("pyweb-devtools main").text_content()
    slider(page, 1)
    until(page, "document.querySelector('#b .counter').textContent === 'b=1'")
    assert page.evaluate("document.documentElement.getAttribute('data-pw-timeline')") == "1"
    page.click("#b .counter")                        # paused: the click is held back
    page.wait_for_timeout(100)
    assert page.text_content("#b .counter") == "b=1"
    slider(page, 0)
    until(page, "document.querySelector('#b .counter').textContent === 'b=0'")
    page.locator("pyweb-devtools [data-timeline-resume]").click()
    until(page, "document.querySelector('#b .counter').textContent === 'b=3'")
    assert page.evaluate("document.documentElement.hasAttribute('data-pw-timeline')") is False
    page.click("#b .counter")                        # live again
    until(page, "document.querySelector('#b .counter').textContent === 'b=4'")
    assert len(page.evaluate("window.__pywebTimeline.steps()")) == 4
    assert page.text_content("#a .counter") == "a=0"


def test_clicking_a_step_jumps_to_it(opened):
    page = opened
    page.click("#a .counter")
    page.click("#b .counter")
    until(page, "document.querySelector('#b .counter').textContent === 'b=1'")
    tab(page)
    page.locator('pyweb-devtools [data-step="1"]').click()
    until(page, "document.querySelector('#b .counter').textContent === 'b=0'")
    assert page.text_content("#a .counter") == "a=1"
    page.locator("pyweb-devtools [data-timeline-resume]").click()
    until(page, "document.querySelector('#b .counter').textContent === 'b=1'")
