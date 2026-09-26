"""Run JS (fakedom + runtime.js + case script) in node, return stdout/stderr."""
from __future__ import annotations

import subprocess
from pathlib import Path

TESTS_DIR = Path(__file__).parent
FAKEDOM = (TESTS_DIR / "fakedom.js").read_text()
SSR_DOM = (TESTS_DIR / "ssr_dom.js").read_text()
RUNTIME = (TESTS_DIR.parent / "pyweb" / "runtime" / "browser" / "runtime.js").read_text()


def extract_app_inner(html: str) -> str:
    """Inner HTML of <div id="app">...</div> (outermost)."""
    start = html.index('<div id="app">') + len('<div id="app">')
    depth = 1
    i = start
    while depth > 0:
        nxt_open = html.find("<div", i)
        nxt_close = html.find("</div>", i)
        if nxt_close < 0:
            raise ValueError("unbalanced divs in SSR html")
        if 0 <= nxt_open < nxt_close:
            depth += 1
            i = nxt_open + 4
        else:
            depth -= 1
            if depth == 0:
                return html[start:nxt_close]
            i = nxt_close + 6
    raise ValueError("unbalanced divs in SSR html")

PRELUDE = """var module = { exports: {} };
%s
%s
var makeDocument = module.exports.makeDocument;
var loadSSR = module.exports.loadSSR;
""" % (FAKEDOM, SSR_DOM)

SETUP = """
var document = makeDocument();
var window = { location: { href: "http://localhost/" } };
var __domReady = null;
document.addEventListener = function (ev, fn) { if (ev === "DOMContentLoaded") __domReady = fn; };
%s
var PyWeb = module.exports;
function __fireReady() { if (__domReady) __domReady(); }
""" % RUNTIME


def run_js(case_script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    src = PRELUDE + SETUP + case_script
    return subprocess.run(
        ["node", "-e", src], capture_output=True, text=True, timeout=timeout
    )
