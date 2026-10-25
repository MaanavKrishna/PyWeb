"""``pyweb dev``: Alt-click an element to open its line in the editor; the inspector shows and edits state."""

import json
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
    from pyweb import App, component

    app = App(title="Inspect")


    @component
    def Counter(label):
        c = 0

        def bump():
            c += 1
        <button class="counter" onclick={bump}>{label}={c}</button>


    @app.page("/")
    def Home():
        n = 0
        <main>
            <h1 id="title">Inspect</h1>
            <div id="a"><Counter label="a" /></div>
            <div id="b"><Counter label="b" /></div>
        </main>
''')

STUB = "import json, sys\nopen(sys.argv[1], 'a').write(json.dumps(sys.argv[2:]) + '\\n')\n"


@pytest.fixture
def dev(tmp_path):
    (tmp_path / "app.pyweb").write_text(APP)
    (tmp_path / "editor.py").write_text(STUB)
    log = tmp_path / "opened.log"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = {**os.environ, "PYTHONPATH": root + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "PYWEB_EDITOR": f'"{sys.executable}" "{tmp_path / "editor.py"}" "{log}" {{file}} {{line}}'}
    proc = subprocess.Popen([sys.executable, "-m", "pyweb.cli", "dev", "app.pyweb", "--port", str(port)],
                            cwd=tmp_path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/"
    for _ in range(300):
        try:
            urllib.request.urlopen(url, timeout=1).read()
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.05)
    yield url, tmp_path, log
    proc.terminate()
    proc.wait(10)


def _opened(log, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if log.exists() and log.read_text().strip():
            return [json.loads(line) for line in log.read_text().splitlines()]
        time.sleep(0.05)
    raise AssertionError("the editor was never started")


def test_alt_click_opens_the_line_and_inspects_component_state(page, dev):
    url, folder, log = dev
    page.goto(url)
    ready(page)
    page.click("#b .counter")
    page.click("#b .counter")
    until(page, "document.querySelector('#b .counter').textContent === 'b=2'")
    line = APP.splitlines().index('    <button class="counter" onclick={bump}>{label}={c}</button>') + 1
    page.click("#b .counter", modifiers=["Alt"])
    assert page.text_content("#b .counter") == "b=2"           # the click went to the inspector, not the app
    (file, opened_line), = _opened(log)
    assert os.path.realpath(file) == os.path.realpath(folder / "app.pyweb") and opened_line == str(line)
    panel = page.locator("pyweb-inspect .panel")
    until(page, "!!document.querySelector('pyweb-inspect').shadowRoot.querySelector('.panel.open')")
    assert "Counter" in panel.text_content() and "#2" in panel.text_content()
    field = page.locator('pyweb-inspect input[data-key="Counter.c#1"]')
    assert field.input_value() == "2"
    field.fill("7")
    field.press("Enter")
    field.dispatch_event("change")
    until(page, "document.querySelector('#b .counter').textContent === 'b=7'")
    assert page.text_content("#a .counter") == "a=0"
    page.click("#b .counter")                                    # an ordinary click still works
    until(page, "document.querySelector('#b .counter').textContent === 'b=8'")
    until(page, "document.querySelector('pyweb-inspect').shadowRoot"
                ".querySelector('input[data-key=\"Counter.c#1\"]').value === '8'")


def test_open_refuses_cross_site_and_outside_files(dev):
    url, folder, log = dev
    (folder.parent / "secret.pyweb").write_text("x")

    def post(chain, **headers):
        req = urllib.request.Request(url + "__pyweb/dev/open", data=json.dumps({"chain": chain}).encode(),
                                     headers={"Content-Type": "application/json", **headers}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    assert post(["app.pyweb:3"], Origin="https://evil.example") == 403
    assert post(["app.pyweb:3"], **{"Sec-Fetch-Site": "cross-site"}) == 403
    assert post(["../secret.pyweb:1", "/etc/passwd:1"]) == 404
    assert not log.exists()
    assert post(["app.pyweb:3"]) == 200
    assert _opened(log)
