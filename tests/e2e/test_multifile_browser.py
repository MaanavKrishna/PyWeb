"""A multi-file app in a real browser."""

from playwright.sync_api import expect

from pyweb.testing import serve
from tests.e2e.conftest import ready
from tests.test_multifile import APP, ICONS, WIDGETS, write


def test_multifile_app_hydrates_and_runs(page, tmp_path):
    path = write(tmp_path, {"app.pyweb": APP, "widgets.pyweb": WIDGETS, "ui/icons.pyweb": ICONS})
    with serve(path) as url:
        page.goto(url)
        ready(page)
        expect(page.locator("[data-pw-root]")).to_have_attribute("data-pw-mode", "hydrated")
        expect(page.locator("section h2")).to_have_text("* Lamp")
        badges = page.locator(".badge")
        expect(badges).to_have_text(["new 0", "sale 0"])
        badges.nth(1).click()
        badges.nth(1).click()
        badges.nth(0).click()
        expect(badges).to_have_text(["new 1", "sale 2"])  # each instance keeps its own state
        page.click("#keep")
        expect(page.locator("#saved")).to_have_text("saved lamp (1)")
