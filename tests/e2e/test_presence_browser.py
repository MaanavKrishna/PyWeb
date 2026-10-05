"""Presence in real browsers: who's here, casts between pages, leaving."""

from playwright.sync_api import expect

from pyweb.testing import serve
from tests.e2e.conftest import ready

APP = '''from pyweb import App, join, presence

app = App()


@app.page("/doc/{name}")
def Doc(name: str):
    room = presence("doc:1")
    here = []
    heard = ""
    me = None

    def on_members(members):
        here = [m["name"] for m in members]

    def on_cast(data, sender):
        heard = data["text"]

    def on_mount():
        me = join(room, {"name": name}, on_members, on_cast)

    def wave():
        me.cast({"text": "hi from " + name})

    <p id="here">{", ".join(here)}</p>
    <p id="heard">{heard}</p>
    <button id="wave" onclick={wave}>wave</button>
'''


def test_people_see_each_other_and_casts(browser, tmp_path):
    (tmp_path / "app.pyweb").write_text(APP)
    with serve(str(tmp_path / "app.pyweb")) as url:
        a = browser.new_context().new_page()
        b = browser.new_context().new_page()
        a.goto(url + "/doc/ada")
        ready(a)
        expect(a.locator("#here")).to_have_text("ada")
        b.goto(url + "/doc/bob")
        ready(b)
        expect(a.locator("#here")).to_have_text("ada, bob")
        expect(b.locator("#here")).to_have_text("ada, bob")
        b.click("#wave")
        expect(a.locator("#heard")).to_have_text("hi from bob")
        expect(b.locator("#heard")).to_have_text("")            # casts go to everyone else
        b.close()
        expect(a.locator("#here")).to_have_text("ada")
