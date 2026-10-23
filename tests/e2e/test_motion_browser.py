"""Motion in a real browser: enter and exit animations, interruptions keeping their velocity,
gestures, variants, layout (FLIP) and shared layout_id, drag, counting numbers, reduced motion,
page transitions, and the frame budget of a 100-row reorder."""

import textwrap

import pytest

from playwright.sync_api import expect

from pyweb.testing import serve
from tests.e2e.conftest import ready, until


def _open(tmp_path, source):
    path = tmp_path / "app.pyweb"
    path.write_text(textwrap.dedent(source))
    return serve(str(path))


def _translate_x(page, selector):
    return page.eval_on_selector(selector, "el => new DOMMatrix(getComputedStyle(el).transform === 'none' ? "
                                           "undefined : getComputedStyle(el).transform).m41 + "
                                           "(parseFloat(getComputedStyle(el).translate) || 0)")


def _x(page, selector):
    """The element's x offset from translate (motion's x)."""
    return page.eval_on_selector(selector, "el => parseFloat((getComputedStyle(el).translate || '0').split(' ')[0]) || 0")


LIST = '''
    from pyweb import App
    app = App(title="Motion")


    @app.page("/")
    def Home():
        items = [1, 2, 3]
        show = True

        def add():
            items = items + [max(items + [0]) + 1]

        def drop():
            items = items[1:]

        def toggle():
            show = not show
        <main>
            <button id="add" onclick={add}>add</button>
            <button id="drop" onclick={drop}>drop</button>
            <button id="toggle" onclick={toggle}>toggle</button>
            <ul id="list">
                for i in items:
                    <li class="row" id={"r" + str(i)} initial={{"opacity": 0, "x": -60}} animate={{"opacity": 1, "x": 0}}
                        exit={{"opacity": 0, "x": 80}} transition={{"duration": 0.6, "type": "tween"}}>Row {i}</li>
            </ul>
            if show:
                <p id="box" animate="fade-up" transition={{"duration": 0.5, "type": "tween"}}>Hello</p>
        </main>
'''


def test_enter_and_exit(page, tmp_path):
    with _open(tmp_path, LIST) as url:
        page.goto(url)
        ready(page)
        until(page, "!!window.pywebMotion")
        until(page, "getComputedStyle(document.querySelector('#r1')).opacity === '1'", timeout=3)
        page.click("#add")
        page.wait_for_selector("#r4")
        page.wait_for_timeout(150)
        mid = _x(page, "#r4")
        assert -60 < mid < 0, mid                                           # entering from initial
        until(page, "getComputedStyle(document.querySelector('#r4')).opacity === '1'", timeout=3)
        assert _x(page, "#r4") == 0
        page.click("#drop")
        page.wait_for_timeout(200)
        assert page.locator("#r1").count() == 1                             # still there, leaving
        assert 0 < _x(page, "#r1") < 80
        assert page.eval_on_selector("#r1", "el => el.inert")
        expect(page.locator("#r1")).to_have_count(0, timeout=3000)          # then gone
        assert page.locator(".row").count() == 3
        page.click("#toggle")                                               # a when branch: preset exit
        page.wait_for_timeout(100)
        assert page.locator("#box").count() == 1
        expect(page.locator("#box")).to_have_count(0, timeout=3000)
        page.click("#toggle")
        expect(page.locator("#box")).to_have_count(1)
        until(page, "getComputedStyle(document.querySelector('#box')).opacity === '1'", timeout=3)


def test_reduced_motion_is_instant(page, tmp_path):
    with _open(tmp_path, LIST) as url:
        page.emulate_media(reduced_motion="reduce")
        page.goto(url)
        ready(page)
        until(page, "!!window.pywebMotion")
        page.click("#add")
        page.wait_for_selector("#r4")
        page.wait_for_timeout(50)
        assert _x(page, "#r4") == 0                                         # transforms jump
        page.click("#drop")
        page.wait_for_timeout(50)
        assert _x(page, "#r1") == 80 or page.locator("#r1").count() == 0


MOVE = '''
    from pyweb import App
    app = App(title="Move")


    @app.page("/")
    def Home():
        x = 0

        def go(e):
            x = 300

        def back(e):
            x = 0
        <main>
            <button id="go" onclick={go}>go</button>
            <button id="back" onclick={back}>back</button>
            <div id="ball" style="width: 40px; height: 40px; background: tomato"
                 animate={{"x": x}} transition={{"type": "spring", "stiffness": 120, "damping": 14}}></div>
            <button id="hover" style="margin-top: 40px" while_hover={{"scale": 1.2}} while_tap={{"scale": 0.8}}
                    transition={{"type": "tween", "duration": 0.15}}>hover</button>
            <ul id="v" initial="hidden" animate="show" transition={{"stagger_children": 0.15}}
                variants={{"hidden": {"opacity": 0}, "show": {"opacity": 1}}}>
                for i in range(4):
                    <li class="v" variants={{"hidden": {"opacity": 0, "y": 20}, "show": {"opacity": 1, "y": 0}}}
                        transition={{"type": "tween", "duration": 0.3}}>{i}</li>
            </ul>
        </main>
'''


def test_interrupting_keeps_velocity_and_gestures(page, tmp_path):
    with _open(tmp_path, MOVE) as url:
        page.goto(url)
        ready(page)
        until(page, "!!window.pywebMotion")
        page.click("#go")
        page.wait_for_timeout(120)
        a = _x(page, "#ball")
        page.click("#back")                                                 # interrupted mid-flight
        samples = []
        for _ in range(4):
            page.wait_for_timeout(16)
            samples.append(_x(page, "#ball"))
        assert a > 5, a
        assert max(samples) > a, (a, samples)                               # momentum carries it on first
        until(page, "Math.abs(parseFloat(getComputedStyle(document.querySelector('#ball')).translate) || 0) < 1",
              timeout=5)

        page.hover("#hover")
        until(page, "getComputedStyle(document.querySelector('#hover')).scale === '1.2'", timeout=2)
        page.mouse.down()
        until(page, "getComputedStyle(document.querySelector('#hover')).scale === '0.8'", timeout=2)
        page.mouse.up()
        until(page, "getComputedStyle(document.querySelector('#hover')).scale === '1.2'", timeout=2)
        page.mouse.move(600, 600)
        until(page, "getComputedStyle(document.querySelector('#hover')).scale === '1'", timeout=2)


def test_variants_stagger_children(page, tmp_path):
    with _open(tmp_path, MOVE) as url:
        page.goto(url)
        ready(page)
        until(page, "!!window.pywebMotion")
        page.wait_for_timeout(200)
        ops = page.eval_on_selector_all(".v", "els => els.map(e => parseFloat(getComputedStyle(e).opacity))")
        assert ops[0] > ops[-1], ops                                         # first ones lead
        until(page, "[...document.querySelectorAll('.v')].every(e => getComputedStyle(e).opacity === '1')", timeout=3)


LAYOUT = '''
    from pyweb import App
    app = App(title="Layout")


    @app.page("/")
    def Home():
        order = [1, 2, 3, 4, 5]
        tab = "a"
        many = list(range(100))

        def flip():
            order = list(reversed(order))

        def shuffle():
            many = many[50:] + many[:50]

        def pick(t):
            tab = t
        <main>
            <button id="flip" onclick={flip}>flip</button>
            <button id="shuffle" onclick={shuffle}>shuffle</button>
            <ul id="rows">
                for i in order:
                    <li class="r" id={"row" + str(i)} layout transition={{"type": "tween", "duration": 0.4}}>
                        <input class="in" value={"v" + str(i)} />
                    </li>
            </ul>
            <nav style="display: flex; gap: 40px">
                for t in ["a", "b", "c"]:
                    <button class="tab" id={"tab-" + t} style="position: relative" onclick={lambda t=t: pick(t)}>
                        {t}
                        if tab == t:
                            <span id="underline" layout_id="underline" transition={{"type": "tween", "duration": 0.4}}
                                  style="position: absolute; left: 0; right: 0; bottom: -4px; height: 2px; background: blue"></span>
                    </button>
            </nav>
            <div id="grid" style="display: grid; grid-template-columns: repeat(10, 30px); gap: 2px">
                for i in many:
                    <div class="cell" layout style="height: 20px; background: #ddd">{i}</div>
            </div>
        </main>
'''


def test_layout_animations_keep_identity(page, tmp_path):
    with _open(tmp_path, LAYOUT) as url:
        page.goto(url)
        ready(page)
        until(page, "!!window.pywebMotion")
        page.wait_for_timeout(300)
        page.locator("#row1 input").fill("typed")
        page.locator("#row1 input").focus()
        top_before = page.eval_on_selector("#row1", "el => el.getBoundingClientRect().top")
        page.click("#flip")
        page.locator("#row1 input").focus()
        page.wait_for_timeout(80)
        mid = page.eval_on_selector("#row1", "el => el.getBoundingClientRect().top")
        final_top = page.eval_on_selector("#row1", "el => el.offsetTop")
        assert mid < final_top + 30, (top_before, mid, final_top)            # still on its way down
        until(page, "!document.querySelector('#row1').getAnimations().length", timeout=3)
        assert page.locator("#row1 input").input_value() == "typed"          # same element: value kept
        assert page.evaluate("document.activeElement.value") == "typed"

        x0 = page.eval_on_selector("#underline", "el => el.getBoundingClientRect().left")
        page.click("#tab-c")
        page.wait_for_timeout(100)
        x_mid = page.eval_on_selector("#underline", "el => el.getBoundingClientRect().left")
        x_end = page.eval_on_selector("#tab-c", "el => el.getBoundingClientRect().left")
        assert x0 < x_mid < x_end, (x0, x_mid, x_end)                        # it slides between tabs
        until(page, "!document.querySelector('#underline').getAnimations().length", timeout=3)


def test_hundred_row_reorder_keeps_frames(page, tmp_path):
    with _open(tmp_path, LAYOUT) as url:
        page.goto(url)
        ready(page)
        until(page, "!!window.pywebMotion")
        page.wait_for_timeout(300)
        page.evaluate("""() => { window.__frames = []; const tick = (t) => { window.__frames.push(t);
                          if (window.__frames.length < 60) requestAnimationFrame(tick); }; requestAnimationFrame(tick); }""")
        page.click("#shuffle")
        until(page, "window.__frames.length >= 60", timeout=5)
        frames = page.evaluate("window.__frames")
        gaps = [b - a for a, b in zip(frames, frames[1:])]
        dropped = sum(1 for g in gaps if g > 1000 / 60 * 1.9)
        assert dropped / len(gaps) < 0.05, gaps


DRAG = '''
    from pyweb import App
    app = App(title="Drag")


    @app.page("/")
    def Home():
        ended = ""

        def done(e):
            ended = "yes"
        <main style="padding: 100px">
            <div id="free" drag="x" drag_constraints={{"left": 0, "right": 200}} drag_elastic={0.2}
                 ondragend={done} style="width: 50px; height: 50px; background: teal"></div>
            <div id="home" drag drag_snap_back={True} style="width: 50px; height: 50px; background: orange; margin-top: 20px"></div>
            <p id="ended">{ended}</p>
            <span id="num" animate="count" transition={{"duration": 0.6}}>{1000 + len(ended) * 1500}</span>
        </main>
'''


def test_drag_and_counting(page, tmp_path):
    with _open(tmp_path, DRAG) as url:
        page.goto(url)
        ready(page)
        until(page, "!!window.pywebMotion")
        page.wait_for_timeout(200)
        box = page.locator("#free").bounding_box()
        page.mouse.move(box["x"] + 25, box["y"] + 25)
        page.mouse.down()
        page.mouse.move(box["x"] + 425, box["y"] + 90, steps=8)               # past the right edge, and down
        past = _x(page, "#free")
        assert 200 < past < 300, past                                        # elastic
        assert page.eval_on_selector("#free", "el => parseFloat(getComputedStyle(el).translate.split(' ')[1] || 0)") == 0
        page.mouse.up()
        expect(page.locator("#ended")).to_have_text("yes")
        until(page, "Math.abs(parseFloat(getComputedStyle(document.querySelector('#free')).translate) - 200) < 1",
              timeout=4)                                                     # settles on the edge

        page.wait_for_timeout(100)
        mid = page.evaluate("document.querySelector('#num').textContent")
        assert mid.isdigit() and 1000 < int(mid) < 5500, mid                   # counting up
        expect(page.locator("#num")).to_have_text("5500", timeout=3000)

        h = page.locator("#home").bounding_box()
        page.mouse.move(h["x"] + 25, h["y"] + 25)
        page.mouse.down()
        page.mouse.move(h["x"] + 125, h["y"] + 75, steps=5)
        page.mouse.up()
        until(page, "(parseFloat(getComputedStyle(document.querySelector('#home')).translate) || 0) < 1", timeout=4)


PAGES = '''
    from pyweb import App
    app = App(title="Pages")


    @app.page("/")
    def Home():
        n = 0

        def inc():
            n += 1
        <main>
            <button onclick={inc}>{n}</button>
            <a id="go" href="/two">two</a>
            <div id="card" layout_id="card" animate="fade">Card</div>
        </main>


    @app.page("/two")
    def Two():
        n = 0

        def inc():
            n += 1
        <main>
            <button onclick={inc}>{n}</button>
            <h1 id="two">Two</h1>
            <div layout_id="card" animate="fade">Card again</div>
        </main>
'''


@pytest.mark.chromium_only            # startViewTransition (Firefox and WebKit swap without it)
def test_client_navigation_uses_view_transitions(page, tmp_path):
    with _open(tmp_path, PAGES) as url:
        page.goto(url)
        ready(page)
        until(page, "!!window.pywebMotion")
        page.evaluate("""() => { window.__vt = 0; const real = document.startViewTransition.bind(document);
                          document.startViewTransition = (cb) => { window.__vt++; return real(cb); }; }""")
        page.click("#go")
        expect(page.locator("#two")).to_be_visible()
        assert page.evaluate("window.__vt") == 1
        assert page.evaluate("location.pathname") == "/two"


LOOPS = '''
    from pyweb import App
    app = App(title="Loops")


    @app.page("/")
    def Home():
        <main style="padding: 40px">
            <div id="spin" animate={{"rotate": [0, 360]}} transition={{"repeat": "infinite", "duration": 0.4, "ease": "linear"}}
                 style="width: 30px; height: 30px; background: purple"></div>
            <div id="shake" animate="shake">shake</div>
            <div id="colour" initial={{"background_color": "rgb(255, 0, 0)"}} animate={{"background_color": "rgb(0, 0, 255)"}}
                 transition={{"duration": 0.3}}>colour</div>
        </main>
'''


def test_repeats_keyframes_and_colours(page, tmp_path):
    with _open(tmp_path, LOOPS) as url:
        page.goto(url)                                                      # no browser code: just the engine
        until(page, "!!window.pywebMotion")
        page.wait_for_timeout(1000)
        assert page.eval_on_selector("#spin", "el => el.getAnimations().some(a => a.playState === 'running')")
        until(page, "!document.querySelector('#shake').getAnimations().length", timeout=3)
        assert _x(page, "#shake") == 0                                      # keyframes end where they began
        until(page, "getComputedStyle(document.querySelector('#colour')).backgroundColor === 'rgb(0, 0, 255)'", timeout=3)
