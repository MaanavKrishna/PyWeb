"""Track A: VLQ sourcemaps roundtrip + CSS extraction bundle."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _node_harness import run_js
from pyweb.compiler.codegen.sourcemap import SourceMap, decode_vlq, encode_vlq
from pyweb.compiler.pipeline import build_text


def test_vlq_roundtrip_values():
    for n in [0, 1, -1, 5, -5, 15, 16, -16, 100, -1000, 123456]:
        assert decode_vlq(encode_vlq(n))[0] == n, (n, encode_vlq(n))


def test_sourcemap_decode_roundtrip():
    from pyweb.compiler.codegen.sourcemap import lookup

    sm = SourceMap(source="app.pyweb")
    sm.add(1, 0, 3, 0)
    sm.add(2, 4, 5, 2)
    sm.add(10, 0, 12, 0)
    d = sm.to_dict("app.js")
    assert d["version"] == 3
    assert d["sources"] == ["app.pyweb"]
    assert d["file"] == "app.js"
    assert lookup(d["mappings"], 1) == (2, 0), lookup(d["mappings"], 1)
    assert lookup(d["mappings"], 2, 4) == (4, 2)
    assert lookup(d["mappings"], 10) == (11, 0)
    assert lookup(d["mappings"], 99) is None


def test_pipeline_exposes_sourcemap_artifact():
    src = """from pyweb import signal

@page("/")
def home():
    n = signal(0)

    <div>
        <p>{n}</p>
    </div>
"""
    art = build_text(src, filename="app.pyweb")
    assert art.sourcemap["version"] == 3
    assert art.sourcemap["sources"] == ["app.pyweb"]
    assert art.sourcemap["mappings"], "mappings must be non-empty"
    with_map = art.js_with_map()
    assert "sourceMappingURL=data:application/json;base64," in with_map
    assert "window.__pyweb_error" in with_map


def test_error_hook_maps_js_line_to_source():
    # mappings for gen line 2 -> app.pyweb line 7 (0-based src line 6)
    sm = SourceMap(source="app.pyweb")
    sm.add(2, 0, 7, 0)
    d = sm.to_dict("app.js")
    import json

    case = (
        "var hook = PyWeb.makeErrorHook(%s);\n"
        "var ov = hook({ message: 'boom' }, 2, 0);\n"
        "console.log(JSON.stringify({ text: ov.textContent, mapped: ov._mapped }));"
        % json.dumps(d)
    )
    r = run_js(case)
    assert r.returncode == 0, r.stderr[-2000:]
    got = json.loads(r.stdout.strip())
    assert got["mapped"]["source"] == "app.pyweb", got
    assert got["mapped"]["line"] == 7, got
    assert "app.pyweb:7" in got["text"], got


def test_css_extraction_hashed_bundle_and_ssr_link():
    src = """from pyweb import signal

@page("/")
def home():
    <style>
        .hero { color: red; }
    </style>
    <div>
        <p css="color: blue;">hi</p>
    </div>
"""
    art = build_text(src, filename="app.pyweb")
    assert art.css_file is not None
    assert art.css_file.endswith(".css")
    assert ".hero" in art.css
    assert "color: blue" in art.css
    assert art.css_file in art.html
    assert "<link" in art.html
    # css prop gone from markup attrs, hashed class present
    assert 'css="' not in art.html


def test_no_css_no_bundle():
    src = """from pyweb import signal

@page("/")
def home():
    <div>plain</div>
"""
    art = build_text(src, filename="app.pyweb")
    assert art.css_file is None
    assert "<link" not in art.html
