"""Customising, measured in a real browser: the theme's accent reaches the components, ui.extend
variants are styled, and Motion(speed=..., feel=...) changes real animation timings."""

import textwrap

from playwright.sync_api import expect

from pyweb.testing import serve
from tests.e2e.conftest import ready, settle, until

THEMED = '''
    from pyweb import App
    from pyweb.ui import Badge, Button, Switch, Theme
    from pyweb import ui

    app = App(title="Themed"{theme})
    ui.extend("Button", variant={{"brand": {{"background": "rgb(1, 2, 3)", "color": "white"}}}})


    @app.page("/")
    def Home():
        <main>
            <Button id="btn">Save</Button>
            <Button id="brand" variant="brand">Brand</Button>
            <Badge id="badge" variant="solid">New</Badge>
            <Switch id="sw" label="On" checked={{True}} />
            <div id="probe" style="background: var(--pw-accent)">probe</div>
        </main>
'''


def _colours(page, tmp_path, theme):
    path = tmp_path / f"app{abs(hash(theme))}.pyweb"
    path.write_text(textwrap.dedent(THEMED.format(theme=theme)))
    with serve(str(path)) as url:
        page.goto(url)
        ready(page)
        settle(page)
        return page.evaluate("""() => {
          const bg = (s) => getComputedStyle(document.querySelector(s)).backgroundColor;
          return {btn: bg("#btn"), brand: bg("#brand"), badge: bg("#badge"), probe: bg("#probe"),
                  switch: getComputedStyle(document.querySelector("#sw")).backgroundColor};
        }""")


def test_theme_accent_reaches_components_and_extend_adds_variants(page, tmp_path):
    default = _colours(page, tmp_path, "")
    pink = _colours(page, tmp_path, ', theme=Theme(accent="#ff0066")')
    assert pink["btn"] == pink["probe"] == pink["badge"] != default["btn"]
    assert pink["switch"] == pink["probe"]                  # a checked switch is the accent too
    assert default["brand"] == pink["brand"] == "rgb(1, 2, 3)"


MOVING = '''
    from pyweb import App
    from pyweb.motion import Motion

    app = App(title="Moving"{motion})


    @app.page("/")
    def Home():
        <main>
            <div id="box" initial={{{{"opacity": 0}}}} animate={{{{"opacity": 1}}}}
                 transition={{{{"duration": 1, "ease": "linear"}}}}>box</div>
        </main>
'''


def _timing(page, tmp_path, motion):
    path = tmp_path / f"m{abs(hash(motion))}.pyweb"
    path.write_text(textwrap.dedent(MOVING.format(motion=motion)))
    with serve(str(path)) as url:
        page.goto(url)                               # no handlers: a static page, motion.js only
        until(page, "!!window.pywebMotion")
        page.wait_for_timeout(50)
        out = page.evaluate("""() => document.querySelector("#box").getAnimations()
                                 .map((a) => Math.round(a.effect.getComputedTiming().duration / 10) * 10)""")
        expect(page.locator("#box")).to_have_css("opacity", "1", timeout=4000)
        return out


def test_motion_speed_and_feel_change_real_durations(page, tmp_path):
    assert _timing(page, tmp_path, "") == [1000]
    assert _timing(page, tmp_path, ', motion=Motion(speed=0.5)') == [2000]
    assert _timing(page, tmp_path, ', motion=Motion(speed=2)') == [500]
    assert _timing(page, tmp_path, ', motion=Motion(feel="none")') == []     # straight to the end
