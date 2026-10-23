"""Test-only assets from the npm registry (checksums verified by pyweb.packages).

    python tests/e2e/fetch_assets.py          # writes the Inter font files the screenshot tests use

axe-core is fetched on demand by tests/e2e/test_a11y_browser.py and cached in tests/e2e/.cache.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from pyweb.packages import Registry  # noqa: E402

INTER = "5.3.0"
AXE = "4.14.0"
WEIGHTS = (400, 500, 600, 700)


def npm_files(name, version):
    reg = Registry()
    _version, meta = reg.resolve(name, version)
    files, _integrity = reg.tarball(meta)
    return files


def fetch_inter():
    files = npm_files("@fontsource/inter", INTER)
    out = os.path.join(HERE, "fonts")
    os.makedirs(out, exist_ok=True)
    for w in WEIGHTS:
        with open(os.path.join(out, f"inter-latin-{w}-normal.woff2"), "wb") as fh:
            fh.write(files[f"files/inter-latin-{w}-normal.woff2"])
    with open(os.path.join(out, "LICENSE"), "wb") as fh:
        fh.write(files["LICENSE"])


def axe_source():
    """axe.min.js, cached."""
    cache = os.path.join(HERE, ".cache", f"axe-{AXE}.min.js")
    if not os.path.exists(cache):
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "wb") as fh:
            fh.write(npm_files("axe-core", AXE)["axe.min.js"])
    with open(cache, encoding="utf-8") as fh:
        return fh.read()


if __name__ == "__main__":
    fetch_inter()
    print("fonts written to", os.path.join(HERE, "fonts"))
