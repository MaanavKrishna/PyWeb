"""examples/ui: the UI kit and motion tour, in a browser."""

from playwright.sync_api import expect

from pyweb.testing import serve
from tests.e2e.conftest import ready, settle, until

APP = "examples/ui/app.pyweb"


def test_components_tab(page):
    with serve(APP) as url:
        page.goto(url)
        ready(page)
        settle(page)
        # Card content inside a tab panel stays visible: only the tabs' own panels are panels.
        expect(page.get_by_role("button", name="Secondary")).to_be_visible()
        page.click("#open-dialog")
        dialog = page.get_by_role("dialog", name="Edit profile")
        expect(dialog).to_be_visible()
        page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        expect(page.locator("#open-dialog")).to_be_focused()
        page.click("#toast-ok")
        expect(page.get_by_text("Changes saved")).to_be_visible()
        page.get_by_role("combobox").fill("fla")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        expect(page.locator("#framework")).to_have_text("Flask")


def test_motion_tab_and_command_palette(page):
    with serve(APP) as url:
        page.goto(url)
        ready(page)
        page.get_by_role("tab", name="Motion").click()
        expect(page.get_by_text("Stagger")).to_be_visible()
        settle(page)
        page.click("#rotate")
        expect(page.locator("#order li")).to_have_text(["Build", "Ship", "Learn", "Plan"])
        page.get_by_role("button", name="Activity").click()
        until(page, "document.querySelector('[aria-pressed=true] .underline') !== null")
        assert page.evaluate("document.querySelectorAll('.underline').length") == 1
        page.get_by_role("tab", name="Data").click()
        expect(page.get_by_role("cell", name="Ada Lovelace")).to_be_visible()
        page.keyboard.press("Control+k")
        page.keyboard.type("data")
        page.keyboard.press("Enter")
        expect(page.locator("#ran")).to_have_text("Command palette: Data table")
        settle(page)
