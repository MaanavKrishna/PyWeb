"""``pyweb test``: finds the tests that ``pyweb new`` writes (test_app.py next to app.pyweb)."""

import os
import subprocess
import sys

import pytest

from pyweb.cli import tests_path as where_tests_run


def test_target_defaults_to_the_current_folder_and_accepts_the_app_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert where_tests_run() == str(tmp_path)
    assert where_tests_run("app.pyweb") == str(tmp_path)
    assert where_tests_run("test_app.py") == str(tmp_path / "test_app.py")


@pytest.mark.parametrize("args", [[], ["app.pyweb"], ["."]])
def test_pyweb_test_runs_a_new_apps_tests(tmp_path, args):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PYTHONPATH": root + os.pathsep + os.environ.get("PYTHONPATH", "")}
    run = lambda *a: subprocess.run([sys.executable, "-m", "pyweb.cli", *a], cwd=tmp_path / "app" if a[0] == "test" else tmp_path,
                                    env=env, capture_output=True, text=True, timeout=300)
    assert run("new", "--template", "counter", "app").returncode == 0
    res = run("test", *args)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "1 passed" in res.stdout


def test_pyweb_test_reports_a_missing_path(tmp_path):
    res = subprocess.run([sys.executable, "-m", "pyweb.cli", "test", "nope"], cwd=tmp_path, capture_output=True, text=True)
    assert res.returncode != 0 and "no such file or folder: nope" in res.stderr


def test_upgrade_mentions_accessibility_findings_once(tmp_path):
    from pyweb import upgrade
    path = tmp_path / "app.pyweb"
    path.write_text("from pyweb import App\napp = App()\n\n@app.page('/')\ndef Home():\n"
                    "    <main>\n        <img src='/a.png'>\n        <input name='q'>\n    </main>\n")
    found = [f for f in upgrade.check(str(path)) if f.code == "a11y"]
    assert len(found) == 1 and "2 here" in found[0].message and "--strict" in found[0].message
    path.write_text("from pyweb import App\napp = App()\n\n@app.page('/')\ndef Home():\n    <p>ok</p>\n")
    assert not [f for f in upgrade.check(str(path)) if f.code == "a11y"]
