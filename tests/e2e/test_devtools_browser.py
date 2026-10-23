"""The dev toolbar's Theme and Motion panels in a browser, on a real `pyweb dev` server."""

import socket
import textwrap
import threading
import time
import urllib.request

import pytest

from tests.e2e.conftest import until

APP = textwrap.dedent('''
    from pyweb import App
    from pyweb.ui import Button

    app = App(title="Tune")


    @app.page("/")
    def Home():
        n = 0

        def inc():
            n += 1
        <main>
            <Button id="btn" onclick={inc}>n={n}</Button>
            <div id="box" initial={{"opacity": 0, "y": 20}} animate={{"opacity": 1, "y": 0}}
                 transition={{"type": "spring", "stiffness": 200, "damping": 20}}>box</div>
        </main>
''')


@pytest.fixture
def dev(tmp_path):
    from pyweb.cli import cmd_dev
    path = tmp_path / "app.pyweb"
    path.write_text(APP)
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()

    class A:
        file = str(path)
        no_reload = True
        host = "127.0.0.1"
    A.port = port
    threading.Thread(target=cmd_dev, args=(A(),), daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    for _ in range(200):
        try:
            urllib.request.urlopen(url, timeout=1).read()
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.05)
    return url, path


def test_theme_panel_previews_and_saves(page, dev):
    url, path = dev
    page.goto(url)
    page.wait_for_selector("pyweb-devtools", state="attached")
    radius = "getComputedStyle(document.querySelector('#btn')).borderTopLeftRadius"
    assert page.evaluate(radius) == "10px"
    page.locator("pyweb-devtools .pill").click()
    page.locator("pyweb-devtools nav [data-tab=theme]").click()
    slider = page.locator("pyweb-devtools [data-f=radius]")
    slider.evaluate("el => { el.value = '20'; el.dispatchEvent(new Event('input', {bubbles: true})); }")
    until(page, f"{radius} === '20px'")                                   # live preview on the real page
    colour = page.locator("pyweb-devtools [data-f=accent]")
    colour.evaluate("el => { el.value = '#ff0066'; el.dispatchEvent(new Event('input', {bubbles: true})); }")
    code = page.locator("pyweb-devtools #theme-code")
    assert code.inner_text() == 'Theme(accent="#ff0066", radius=20)'
    page.locator("pyweb-devtools [data-save-theme]").click()
    until(page, "!!document.querySelector('pyweb-devtools').shadowRoot.querySelector('.ok')")
    src = path.read_text()
    assert 'app = App(title="Tune", theme=Theme(accent="#ff0066", radius=20))' in src
    assert "from pyweb.ui import Button\nfrom pyweb.ui import Theme\n" in src or "import Button, Theme" in src


def test_motion_panel_tunes_and_replays(page, dev):
    url, _path = dev
    page.goto(url)
    page.wait_for_selector("pyweb-devtools", state="attached")
    until(page, "!!window.pywebMotion")
    page.locator("pyweb-devtools .pill").click()
    page.locator("pyweb-devtools nav [data-tab=motion]").click()
    page.locator("pyweb-devtools [data-pick]").click()
    page.click("#box")
    stiff = page.locator("pyweb-devtools [data-m=stiffness]")
    assert stiff.input_value() == "200"
    stiff.evaluate("el => { el.value = '800'; el.dispatchEvent(new Event('change', {bubbles: true})); }")
    spec = page.evaluate("JSON.parse(document.querySelector('#box').dataset.pwMotion)")
    assert spec["transition"]["stiffness"] == 800 and spec["animate"] == {"opacity": 1, "y": 0}
    assert "'stiffness': 800" not in page.locator("pyweb-devtools #motion-code").inner_text()
    assert '"stiffness":800' in page.locator("pyweb-devtools #motion-code").inner_text()
    page.wait_for_timeout(30)
    assert page.evaluate("getComputedStyle(document.querySelector('#box')).opacity") != "1"   # replaying
    until(page, "getComputedStyle(document.querySelector('#box')).opacity === '1'", timeout=3)
