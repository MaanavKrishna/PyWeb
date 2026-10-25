"""``pyweb dev`` keeps page variables, form fields, scroll and focus when it reloads after a save."""

import os
import socket
import subprocess
import sys
import textwrap
import time
import urllib.request

import pytest

from tests.e2e.conftest import ready, until

APP = textwrap.dedent('''
    from pyweb import App

    app = App(title="Keep")


    @app.page("/")
    def Home():
        n = 0
        name = ""

        def inc():
            n += 1
        <main>
            <h1 id="title">v1</h1>
            <button id="btn" onclick={inc}>n={n}</button>
            <label>Name <input id="name" bind={name} /></label>
            <p id="hello">hello {name}</p>
            <label>Note <input id="note" /></label>
            <div style="height: 3000px">tall</div>
        </main>
''')


@pytest.fixture
def dev(tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(APP)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = {**os.environ, "PYTHONPATH": root + os.pathsep + os.environ.get("PYTHONPATH", "")}
    proc = subprocess.Popen([sys.executable, "-m", "pyweb.cli", "dev", "app.pyweb", "--port", str(port)],
                            cwd=tmp_path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/"
    for _ in range(300):
        try:
            urllib.request.urlopen(url, timeout=1).read()
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.05)
    yield url, path
    proc.terminate()
    proc.wait(10)


def after_reload(pg, expression, timeout=10.0):
    """Wait for a condition across the reload: evaluating while the page navigates throws."""
    from playwright.sync_api import Error
    end = time.monotonic() + timeout
    while True:
        try:
            if pg.evaluate(expression):
                return
        except Error:
            pass                          # mid-navigation; try again on the new page
        if time.monotonic() > end:
            raise AssertionError(f"timed out waiting for {expression}")
        pg.wait_for_timeout(25)


def edit(path, old, new):
    text = path.read_text()
    assert old in text
    time.sleep(1.1)                       # the watcher compares modification times
    path.write_text(text.replace(old, new))


def test_state_survives_a_reload_after_a_save(page, dev):
    url, path = dev
    page.goto(url)
    ready(page)
    for _ in range(3):
        page.click("#btn")
    page.fill("#name", "Ann")
    page.fill("#note", "remember me")
    page.focus("#note")
    page.evaluate("document.querySelector('#note').setSelectionRange(2, 5)")
    page.evaluate("scrollTo(0, 600)")
    until(page, "document.querySelector('#btn').textContent === 'n=3'")
    edit(path, '<h1 id="title">v1</h1>', '<h1 id="title">v2</h1>')
    after_reload(page, "document.querySelector('#title')?.textContent === 'v2' && "
                       "document.documentElement.hasAttribute('data-pw-dev-restored')")
    assert page.text_content("#btn") == "n=3"
    assert page.input_value("#name") == "Ann" and page.text_content("#hello") == "hello Ann"
    assert page.input_value("#note") == "remember me"
    assert page.evaluate("document.activeElement.id") == "note"
    assert page.evaluate("[document.activeElement.selectionStart, document.activeElement.selectionEnd]") == [2, 5]
    assert abs(page.evaluate("scrollY") - 600) < 5
    page.click("#btn")                    # still reactive after the hand-over
    until(page, "document.querySelector('#btn').textContent === 'n=4'")


def test_a_variable_whose_type_changed_starts_over(page, dev):
    url, path = dev
    page.goto(url)
    ready(page)
    page.click("#btn")
    until(page, "document.querySelector('#btn').textContent === 'n=1'")
    edit(path, "n = 0\n", 'n = "zero"\n')
    edit(path, "n += 1", 'n = n + "!"')
    after_reload(page, "document.querySelector('#btn')?.textContent === 'n=zero'")


def test_fresh_skips_restoring(page, dev):
    url, path = dev
    page.goto(url + "?fresh")
    ready(page)
    page.click("#btn")
    until(page, "document.querySelector('#btn').textContent === 'n=1'")
    edit(path, '<h1 id="title">v1</h1>', '<h1 id="title">v2</h1>')
    after_reload(page, "document.querySelector('#title')?.textContent === 'v2' && "
                       "!!document.querySelector('[data-pw-ready]')")
    page.wait_for_timeout(200)
    assert page.text_content("#btn") == "n=0"


HOT = textwrap.dedent('''
    from pyweb import App, component

    app = App(title="Hot", stylesheets=["/static/app.css"])


    @component
    def Counter(label):
        c = 0

        def bump():
            c += 1
        <button class="counter" onclick={bump}>{label}={c}</button>


    @app.page("/")
    def Home():
        n = 0

        def inc():
            n += 1
        <main>
            <h1 id="title">v1</h1>
            <button id="btn" onclick={inc}>n={n}</button>
            <div id="a"><Counter label="a" /></div>
            <div id="b"><Counter label="b" /></div>
        </main>
''')


@pytest.fixture
def hot(tmp_path):
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "app.css").write_text("h1 { color: rgb(255, 0, 0); }\n")
    path = tmp_path / "app.pyweb"
    path.write_text(HOT)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = {**os.environ, "PYTHONPATH": root + os.pathsep + os.environ.get("PYTHONPATH", "")}
    proc = subprocess.Popen([sys.executable, "-m", "pyweb.cli", "dev", "app.pyweb", "--port", str(port)],
                            cwd=tmp_path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/"
    for _ in range(300):
        try:
            urllib.request.urlopen(url, timeout=1).read()
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.05)
    yield url, path
    proc.terminate()
    proc.wait(10)


def _clicks(page):
    for _ in range(3):
        page.click("#btn")
    page.click("#a .counter")
    for _ in range(2):
        page.click("#b .counter")
    until(page, "document.querySelector('#b .counter').textContent === 'b=2'")
    page.evaluate("window.__mark = 1")    # survives only if the page never reloads


def test_code_change_swaps_in_place_and_keeps_component_state(page, hot):
    url, path = hot
    page.goto(url)
    ready(page)
    _clicks(page)
    edit(path, '<h1 id="title">v1</h1>', '<h1 id="title">v2</h1>')
    after_reload(page, "document.querySelector('#title')?.textContent === 'v2' && "
                       "document.documentElement.hasAttribute('data-pw-dev-restored')")
    assert page.evaluate("window.__mark") == 1
    assert page.text_content("#btn") == "n=3"
    assert page.text_content("#a .counter") == "a=1"
    assert page.text_content("#b .counter") == "b=2"
    page.click("#a .counter")             # still reactive
    until(page, "document.querySelector('#a .counter').textContent === 'a=2'")


def test_css_change_swaps_the_stylesheet_only(page, hot):
    url, path = hot
    page.goto(url)
    ready(page)
    _clicks(page)
    css = path.parent / "static" / "app.css"
    time.sleep(1.1)
    css.write_text("h1 { color: rgb(0, 0, 255); }\n")
    until(page, "getComputedStyle(document.querySelector('#title')).color === 'rgb(0, 0, 255)'", timeout=10)
    assert page.evaluate("window.__mark") == 1
    assert page.text_content("#b .counter") == "b=2"
    assert page.evaluate("document.querySelectorAll('link[rel=stylesheet][href*=\"app.css\"]').length") == 1


def test_state_comes_back_after_a_compile_error_is_fixed(page, hot):
    url, path = hot
    page.goto(url)
    ready(page)
    _clicks(page)
    edit(path, '<h1 id="title">v1</h1>', '<h1 id="title">v1</h2>')
    after_reload(page, "!document.querySelector('[data-pw-root]')")
    edit(path, '<h1 id="title">v1</h2>', '<h1 id="title">v3</h1>')
    after_reload(page, "document.querySelector('#title')?.textContent === 'v3' && "
                       "document.documentElement.hasAttribute('data-pw-dev-restored')")
    assert page.text_content("#btn") == "n=3"
    assert page.text_content("#a .counter") == "a=1"
    assert page.text_content("#b .counter") == "b=2"
