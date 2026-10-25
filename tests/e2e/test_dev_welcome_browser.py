"""``pyweb dev``: the first time a project runs, a welcome panel; Got it hides it for good."""

import os
import socket
import subprocess
import sys
import time
import urllib.request

from tests.e2e.conftest import ready, until


def test_welcome_once(page, tmp_path):
    (tmp_path / "app.pyweb").write_text('from pyweb import App\napp = App()\n\n@app.page("/")\ndef Home():\n'
                                        '    n = 0\n\n    def inc():\n        n += 1\n'
                                        '    <button id="b" onclick={inc}>{n}</button>\n')
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = {**os.environ, "PYTHONPATH": root, "PYWEB_WELCOME": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "pyweb.cli", "dev", "app.pyweb", "--port", str(port)], cwd=tmp_path,
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/"
    try:
        for _ in range(300):
            try:
                urllib.request.urlopen(url, timeout=1).read()
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.05)
        page.goto(url)
        ready(page)
        card = page.locator("pyweb-welcome .card")
        assert "Welcome to PyWeb" in card.text_content() and "claude mcp add pyweb" in card.text_content()
        page.locator("pyweb-welcome [data-done]").click()
        until(page, "!document.querySelector('pyweb-welcome')")
        for _ in range(50):
            if (tmp_path / ".pyweb" / "welcomed").exists():
                break
            time.sleep(0.05)
        page.reload()
        ready(page)
        assert page.evaluate("!document.querySelector('pyweb-welcome')")
    finally:
        proc.terminate()
        proc.wait(15)
