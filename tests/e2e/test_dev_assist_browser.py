"""``pyweb dev``: Fix with AI on the error page and Alt+Shift-click → Edit with AI, against a fake model."""

import os
import socket
import subprocess
import sys
import textwrap
import time
import urllib.request

import pytest

from tests.e2e.conftest import ready, until
from tests.e2e.test_dev_reload_browser import after_reload, edit

GOOD = textwrap.dedent('''
    from pyweb import App

    app = App(title="Assist")


    @app.page("/")
    def Home():
        n = 0

        def inc():
            n += 1
        <main>
            <h1 id="title">Hello</h1>
            <button id="btn" onclick={inc}>n={n}</button>
        </main>
''')


def shadow(selector):
    return f"document.querySelector('pyweb-assist').shadowRoot.querySelector({selector!r})"


@pytest.fixture
def dev(tmp_path):
    (tmp_path / "app.pyweb").write_text(GOOD)
    reply = tmp_path / "reply.txt"
    reply.write_text("nothing yet")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = {**os.environ, "PYTHONPATH": root + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "PYWEB_AI_FAKE": str(reply)}
    proc = subprocess.Popen([sys.executable, "-m", "pyweb.cli", "dev", "app.pyweb", "--port", str(port)],
                            cwd=tmp_path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/"
    for _ in range(300):
        try:
            urllib.request.urlopen(url, timeout=1).read()
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.05)
    yield url, tmp_path / "app.pyweb", reply
    proc.terminate()
    proc.wait(15)


def test_fix_with_ai_on_the_compile_error_page_then_undo(page, dev):
    url, path, reply = dev
    page.goto(url)
    ready(page)
    edit(path, '<h1 id="title">Hello</h1>', '<h1 id="title">Hello</h2>')
    after_reload(page, "!!document.querySelector('[data-pw-error]')")
    reply.write_text("I closed the h1 tag.\n```pyweb\n" + GOOD.replace("Hello", "Fixed") + "```\n")
    until(page, f"!{shadow('.fab')}.hidden")
    page.locator("pyweb-assist .fab").click()
    until(page, f"!!{shadow('.panel.open pre.diff')}", timeout=15)
    text = page.locator("pyweb-assist .panel").text_content()
    assert "I closed the h1 tag." in text and "Fixed</h1>" in text and "Hello</h2>" in text
    page.locator("pyweb-assist [data-apply]").click()
    after_reload(page, "document.querySelector('#title')?.textContent === 'Fixed'")
    assert "Fixed</h1>" in path.read_text()
    until(page, f"!!{shadow('.toast.open [data-undo]')}")
    page.locator("pyweb-assist [data-undo]").click()
    after_reload(page, "!!document.querySelector('[data-pw-error]')")
    assert "Hello</h2>" in path.read_text()


def test_alt_shift_click_edits_an_element_and_keeps_state(page, dev):
    url, path, reply = dev
    page.goto(url)
    ready(page)
    page.click("#btn")
    page.click("#btn")
    until(page, "document.querySelector('#btn').textContent === 'n=2'")
    reply.write_text("```pyweb\n" + GOOD.replace('<h1 id="title">Hello</h1>', '<h1 id="title">Hello, world</h1>') + "```")
    page.click("#title", modifiers=["Alt", "Shift"])
    until(page, f"!!{shadow('.ask.open textarea')}")
    page.locator("pyweb-assist .ask textarea").fill("greet the whole world")
    page.locator("pyweb-assist [data-edit]").click()
    until(page, f"!!{shadow('.panel.open [data-apply]')}", timeout=15)
    assert "Hello, world" in page.locator("pyweb-assist .panel").text_content()
    page.locator("pyweb-assist [data-apply]").click()
    after_reload(page, "document.querySelector('#title')?.textContent === 'Hello, world'")
    assert page.text_content("#btn") == "n=2"          # hot reload kept the page's state


def test_fix_with_ai_on_a_runtime_error(page, dev):
    url, path, reply = dev
    time.sleep(1.1)
    path.write_text(GOOD.replace("n = 0\n", "n = 1 // 0\n"))
    time.sleep(0.5)
    reply.write_text("```pyweb\n" + GOOD + "```")
    page.goto(url)
    until(page, f"!{shadow('.fab')}.hidden")
    page.locator("pyweb-assist .fab").click()
    until(page, f"!!{shadow('.panel.open pre.diff')}", timeout=15)
    assert "1 // 0" in page.locator("pyweb-assist .panel").text_content()
