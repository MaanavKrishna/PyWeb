"""Every PYWEB_* setting PyWeb reads is documented, so nobody has to read the source to find it."""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Set by PyWeb itself for its own child processes, or only for PyWeb's own tests.
INTERNAL = {
    "PYWEB_AI_FAKE",            # tests: a canned model answer for Fix/Edit with AI
    "PYWEB_BODY", "PYWEB_TITLE",  # desktop: a notification's text, passed to the helper process
    "PYWEB_DESKTOP", "PYWEB_DESKTOP_NAME", "PYWEB_TARGET",   # set by `pyweb desktop` for the app it starts
    "PYWEB_METRICS_DIR",        # set by `pyweb serve --workers` for its worker processes
    "PYWEB_NO_AUTO_MIGRATE",    # set while packaging a desktop app
    "PYWEB_NO_KIT_CACHE",       # working on PyWeb's UI kit itself
}


def _read():
    names = set()
    for folder, _dirs, files in os.walk(os.path.join(ROOT, "pyweb")):
        if "templates" in folder.split(os.sep):
            continue
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(folder, name), encoding="utf-8") as fh:
                    names |= set(re.findall(r"[\"'](PYWEB_[A-Z0-9_]+)[\"']", fh.read()))
    return names


def test_every_setting_is_documented():
    docs = ""
    for name in sorted(os.listdir(os.path.join(ROOT, "docs"))):
        if name.endswith(".md"):
            with open(os.path.join(ROOT, "docs", name), encoding="utf-8") as fh:
                docs += fh.read()
    missing = sorted(n for n in _read() - INTERNAL if not re.search(rf"\b{n}\b", docs))
    assert not missing, f"document these in docs/ (or mark them internal here): {missing}"


def test_settings_have_one_reader():
    """The server settings are parsed in one place (config.py), which validates them at startup."""
    from pyweb import config
    for name in ("PYWEB_THREADS", "PYWEB_MAX_QUEUE", "PYWEB_SHUTDOWN_TIMEOUT", "PYWEB_RENDER_TIMEOUT"):
        assert name in config._KEYS and name in config.__doc__
