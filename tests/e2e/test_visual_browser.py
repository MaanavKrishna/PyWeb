"""Screenshot baselines: every UI kit component family, light and dark, desktop and phone, plus the
auth kit's sign-in page and /admin. Compared in Chromium (the baselines are Chromium renders); other
engines render the same shots and only check for errors. See visual.py."""

import os
import textwrap

import pytest

from pyweb.testing import serve
from tests.e2e import visual
from tests.e2e.conftest import ENGINE, ready, settle

GALLERY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gallery.pyweb")
DESKTOP = {"width": 1000, "height": 900}
PHONE = {"width": 390, "height": 844}

# (viewport name, size, colour scheme, sections)
RUNS = [
    ("desktop", DESKTOP, "light", ["buttons", "forms", "disclosure", "data", "calendar"]),
    ("desktop", DESKTOP, "dark", ["buttons", "forms", "disclosure", "data", "calendar"]),
    ("phone", PHONE, "light", ["buttons", "forms", "data"]),
]


@pytest.fixture
def shoot(browser):
    """A page in a context that lets the test add its stylesheet, failing on page errors."""
    contexts = []

    def make(viewport, scheme):
        ctx = browser.new_context(bypass_csp=True, viewport=viewport, color_scheme=scheme,
                                  reduced_motion="reduce", device_scale_factor=1)
        contexts.append(ctx)
        pg = ctx.new_page()
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
        return pg
    yield make
    for ctx in contexts:
        errors = [e for p in ctx.pages for e in p.errors if "Failed to load resource" not in e]
        ctx.close()
        assert not errors, errors


def check(browser, name, png):
    if ENGINE == "chromium":
        visual.compare(browser, name, png)


@pytest.mark.parametrize("viewport,size,scheme,sections", RUNS, ids=[f"{r[0]}-{r[2]}" for r in RUNS])
def test_component_gallery(browser, shoot, viewport, size, scheme, sections):
    with serve(GALLERY) as url:
        page = shoot(size, scheme)
        page.goto(url)
        ready(page)
        settle(page)
        visual.stabilize(page)
        for section in sections:
            check(browser, f"{section}-{viewport}-{scheme}", page.locator(f"#{section}").screenshot())
        if viewport == "desktop":
            page.click("#dialog-open")
            dialog = page.get_by_role("dialog", name="Edit profile")
            dialog.wait_for()
            check(browser, f"dialog-{viewport}-{scheme}", dialog.screenshot())
            page.keyboard.press("Escape")
            dialog.wait_for(state="hidden")
            page.click("#menu-open")
            menu = page.get_by_role("menu")
            menu.wait_for()
            check(browser, f"menu-{viewport}-{scheme}", menu.screenshot())
            page.keyboard.press("Escape")
            page.click("#popover-open")
            pop = page.locator(".pw-popover")
            pop.wait_for()
            check(browser, f"popover-{viewport}-{scheme}", pop.screenshot())
        settle(page)


AUTH_APP = textwrap.dedent('''
    from pyweb import App, Model

    app = App(title="Shots", database="sqlite:///shots.db")
    auth = app.use_auth(signup="instant")


    class Note(Model):
        text: str


    @app.page("/")
    def Home():
        <p>home</p>
''')


@pytest.mark.parametrize("viewport,size", [("desktop", DESKTOP), ("phone", PHONE)])
def test_auth_and_admin_pages(browser, shoot, tmp_path, monkeypatch, viewport, size):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYWEB_BREACHED_PASSWORDS", "0")
    (tmp_path / "app.pyweb").write_text(AUTH_APP)
    with serve(str(tmp_path / "app.pyweb")) as url:
        url = url.rstrip("/") + "/"
        from pyweb.authkit import User
        from pyweb.authkit.passwords import hash_password
        from pyweb.models import Model, system
        with system():
            User(email="admin@example.com", name="Admin", roles=["admin"], email_verified=True,
                 password_hash=hash_password("correct horse battery")).save()
            note = next(m for m in Model.__subclasses__() if m.__name__ == "Note")
            for text in ("Buy milk", "Call the bank", "Water the plants"):
                note.create(text=text)
        page = shoot(size, "light")
        page.goto(url + "login")
        visual.stabilize(page)
        check(browser, f"login-{viewport}", page.screenshot())
        page.fill("input[name=email]", "admin@example.com")
        page.fill("input[name=password]", "correct horse battery")
        page.click("button[type=submit]")
        page.wait_for_url("**/")
        page.goto(url + "admin/notes")
        visual.stabilize(page)
        check(browser, f"admin-{viewport}", page.screenshot())
