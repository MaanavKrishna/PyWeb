"""Codegen: HTML/JS emitters, IR text, py2js lowering, sourcemaps."""

from pathlib import Path

from pyweb.compiler import compile_source


def test_ssr_escapes_html():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    v = '<b>&'\n    <p>{v}</p>\n")
    assert "&lt;b&gt;" in out["pages"]["H"]["html"]


def test_static_page_has_no_runtime():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <h1>Hi</h1>\n")
    assert "runtime.js" not in out["pages"]["H"]["js"]
    assert "<h1>Hi</h1>" in out["pages"]["H"]["html"]


def test_dynamic_page_ships_module_and_state():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    n = 0\n    def inc():\n        n += 1\n    <button onclick={inc}>{n}</button>\n")
    page = out["pages"]["H"]
    assert '<div data-pw-root="H"><button>0</button></div>' in page["html"]
    assert '<script id="pw-state" type="application/json">{"n":0}</script>' in page["html"]
    assert '$mount("H", H);' in page["js"] and '"onclick": inc' in page["js"]


def test_handler_lowering_augassign():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    n = 0\n    def inc():\n        n += 1\n    <button onclick={inc}>{n}</button>\n")
    assert "n($py.add(n(), 1));" in out["pages"]["H"]["js"]


def test_list_mutation_is_copy_on_write():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    xs = []\n    def add():\n        xs.append(1)\n    <button onclick={add}>{len(xs)}</button>\n")
    js = out["pages"]["H"]["js"]
    assert '$py.mut(xs, [], ($v) => $py.m($v, "append", 1));' in js
    assert "() => $py.len(xs())" in js


def test_ir_text_lists_placement():
    out = compile_source(Path("examples/counter/app.pyweb").read_text())
    assert "place count: browser" in out["ir_text"]


def test_sourcemap_entries():
    out = compile_source(Path("examples/counter/app.pyweb").read_text())
    sm = out["pages"]["Home"]["sourcemap"]
    kinds = [k for k, _ in sm]
    assert any(k.startswith("sig ") for k in kinds)
    assert any(k.startswith("handler ") for k in kinds)


def test_full_page_shell():
    out = compile_source("from pyweb import App\napp=App(title='T')\n@app.page('/')\ndef H():\n    n = 0\n    <h1 onclick={lambda: None}>{n}</h1>\n")
    html = out["pages"]["H"]["html"]
    assert html.startswith("<!doctype html>") and "<title>T</title>" in html
    assert '<script type="module" src="/static/H.js?v=' in html


def test_for_loop_ssr_static_items():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    items = ['a', 'b']\n    for i in items:\n        <p>{i}</p>\n")
    assert out["pages"]["H"]["html_body"].count("<p>") == 2


def test_unknown_component_is_a_compile_error():
    import pytest
    from pyweb.compiler.errors import CompileError
    with pytest.raises(CompileError) as e:
        compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <UserCard name=\"x\" />\n")
    assert "unknown component <UserCard>" in str(e.value) and ":5:" in str(e.value)


def test_component_renders_with_props_and_children():
    src = ("from pyweb import App\napp=App()\n\ndef Card(title, children=None):\n"
           "    <section><h2>{title}</h2>{children}</section>\n\n"
           "@app.page('/')\ndef H():\n    <Card title=\"Hi\"><p>body</p></Card>\n")
    out = compile_source(src)
    assert "<section><h2>Hi</h2><p>body</p></section>" in out["pages"]["H"]["html_body"]


def test_ssr_compiles_each_expression_once():
    from pyweb.ssr import Renderer, RenderError, _compiled
    r = Renderer({"n": 2})
    _compiled.cache_clear()
    for _ in range(5):
        assert r.eval("n * 21", {}) == 42
    assert _compiled.cache_info().misses == 1 and _compiled.cache_info().hits == 4
    try:
        r.eval("n +", {}, 3)
    except RenderError as exc:
        assert exc.lineno == 3
    else:
        raise AssertionError("syntax error not reported")

