"""The UI kit in a real browser: attribute pass-through, kit components hydrating, and (as the kit
grows) every primitive's keyboard behaviour."""

import textwrap

from playwright.sync_api import expect

from pyweb.testing import serve
from tests.e2e.conftest import ready

SPREAD = textwrap.dedent('''
    from pyweb import App
    from pyweb.ui import Button, Badge

    app = App(title="Spread")


    def Field(label, **attrs):
        <label>{label} <input class="in" {**attrs} /></label>


    @app.page("/")
    def Home():
        n = 0
        on = False

        def inc():
            n += 1

        def flip():
            on = not on
        extra = {"data-on": "yes", "class": "lit"} if on else {"title": "off"}
        <main>
            <Button id="inc" onclick={inc} aria-label={"count " + str(n)} class_="mine">n={n}</Button>
            <Button id="flip" variant="outline" onclick={flip}>flip</Button>
            <div id="box" class="box" {**extra}>box</div>
            <Field label="Name" id="name" placeholder={"P" + str(n)} required={on} />
            <Badge id="badge" tone={"success" if on else None}>b</Badge>
        </main>
''')


def test_attributes_pass_through_in_the_browser(page, tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(SPREAD)
    with serve(str(path)) as url:
        page.goto(url)
        ready(page)
        btn = page.locator("#inc")
        expect(btn).to_have_class("pw-button mine")
        page.click("#inc")
        expect(btn).to_have_text("n=1")                                   # the handler came through **attrs
        expect(btn).to_have_attribute("aria-label", "count 1")            # and its attributes follow state
        expect(page.locator("#name")).to_have_attribute("placeholder", "P1")
        box = page.locator("#box")
        expect(box).to_have_attribute("title", "off")
        expect(box).to_have_class("box")
        page.click("#flip")
        expect(box).to_have_class("box lit")                              # classes merge
        expect(box).to_have_attribute("data-on", "yes")
        assert box.get_attribute("title") is None                         # keys the dict dropped are removed
        expect(page.locator("#name")).to_have_attribute("required", "")
        expect(page.locator("#badge")).to_have_attribute("data-tone", "success")
        page.click("#flip")
        expect(box).to_have_class("box")
        assert box.get_attribute("data-on") is None
