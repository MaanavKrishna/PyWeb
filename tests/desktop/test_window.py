"""The desktop app in a real window (pywebview). Skipped without pywebview or a display; CI runs it
under xvfb (the `desktop` job), locally: xvfb-run -a pytest tests/desktop."""

import os
import subprocess
import sys

import pytest

pytest.importorskip("webview")
if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or sys.platform in ("win32", "darwin")):
    pytest.skip("no display (run under xvfb-run)", allow_module_level=True)

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
APP = '''
from pyweb import App, Model, Field, platform
app = App(title="Window Check", database="sqlite:///notes.db")


class Note(Model):
    text: str = Field(max=100)


@app.page("/")
def Home():
    count = Note.query().count()
    where = "desktop" if platform.desktop else "web"
    <main><h1>{where}</h1><p>{count} notes</p><a href="https://example.com">out</a></main>
'''


def test_the_window_opens_the_app(tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(APP)
    data = tmp_path / "data"
    env = {**os.environ, "PYWEB_DESKTOP_DATA": str(data), "QTWEBENGINE_DISABLE_SANDBOX": "1",
           "WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS": "1"}
    for name in ("PYWEB_ENV", "PYWEB_AUTH_SECRET", "PYWEB_DATA_KEY", "PYWEB_DESKTOP"):
        env.pop(name, None)
    gui = os.environ.get("PYWEB_TEST_GUI")
    cmd = [sys.executable, "-m", "pyweb.cli", "desktop", str(path), "--smoke"] + (["--gui", gui] if gui else [])
    done = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert "pyweb desktop smoke: ok Window Check" in done.stdout, done.stdout + done.stderr
    assert done.returncode == 0
    # The database, the secrets and the window's storage are in the data folder.
    assert (data / "notes.db").is_file() and (data / "secrets.json").is_file() and (data / "webview").is_dir()
