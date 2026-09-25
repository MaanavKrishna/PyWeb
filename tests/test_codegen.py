"""Codegen: HTML/JS emitters, IR text, py2js lowering, sourcemaps."""

from pyweb.compiler import compile_source
from pyweb.compiler.codegen.emit_js import _py2js
from pyweb.compiler.codegen.ir import to_text


def test_ssr_escapes_html():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    v = '<b>&'\n    <p>{v}</p>\n")
    assert "&lt;b&gt;" in out["pages"]["H"]["html"]


def test_static_page_has_no_runtime():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <h1>Hi</h1>\n")
    assert "runtime.js" not in out["pages"]["H"]["js"]
    assert "<h1>Hi</h1>" in out["pages"]["H"]["html"]


def test_dynamic_binding_markers():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    n = 0\n    def inc():\n        n += 1\n    <button onclick={inc}>{n}</button>\n")
    page = out["pages"]["H"]
    assert 'pw-bind="n"' in page["html_body"]
    assert "bind_text" in page["js"] and "increment" not in page["js"].replace("inc", "") or "inc" in page["js"]


def test_handler_lowering_augassign():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    n = 0\n    def inc():\n        n += 1\n    <button onclick={inc}>{n}</button>\n")
    assert "n(n() + 1)" in out["pages"]["H"]["js"]


def test_py2js_keywords():
    assert _py2js("a and not b") == "a && ! b"
    assert _py2js("x is None") != ""
    assert "count()" in _py2js("count", signals=["count"])


def test_ir_text_lists_placement():
    out = compile_source(open("examples/counter/app.pyweb").read())
    assert "place count: browser" in out["ir_text"]


def test_sourcemap_entries():
    out = compile_source(open("examples/counter/app.pyweb").read())
    sm = out["pages"]["Home"]["sourcemap"]
    kinds = [k for k, _ in sm]
    assert any(k.startswith("sig ") for k in kinds)
    assert any(k.startswith("handler ") for k in kinds)


def test_full_page_shell():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <h1>T</h1>\n")
    html = out["pages"]["H"]["html"]
    assert html.startswith("<!doctype html>") and "/static/H.js" in html


def test_for_loop_ssr_static_items():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    items = ['a', 'b']\n    for i in items:\n        <p>{i}</p>\n")
    assert out["pages"]["H"]["html_body"].count("<p>") == 2


def test_component_tag_renders_div():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <UserCard name=\"x\" />\n")
    assert "<div" in out["pages"]["H"]["html_body"]
