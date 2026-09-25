"""Reactivity: signals, computeds, Python primitives, edges."""

import ast

from pyweb import Computed, Effect, Signal, live
from pyweb.compiler import parser as P
from pyweb.compiler.reactivity import compute_reactive


def _fn_ui(src):
    tree, ui_all, _ = P.parse_source(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
    span = (fn.lineno, getattr(fn, "end_lineno", fn.lineno))
    ui = [n for n in ui_all if span[0] <= getattr(n, "line", 0) <= span[1]]
    return fn, ui


def test_counter_signal():
    fn, ui = _fn_ui("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    count = 0\n    def inc():\n        count += 1\n    <button onclick={inc}>{count}</button>\n")
    signals, computeds, edges = compute_reactive(fn, ui)
    assert signals == ["count"] and not computeds
    assert ("count", "__dom__") in edges


def test_computed_derivation():
    fn, ui = _fn_ui("from pyweb import App\napp=App()\n@app.page('/')\ndef S():\n    price = 100\n    quantity = 2\n    total = price * quantity\n    <p>{total}</p>\n")
    signals, computeds, edges = compute_reactive(fn, ui)
    assert computeds["total"]["deps"] == ["price", "quantity"]
    assert ("price", "total") in edges and ("total", "__dom__") in edges
    assert "total" not in signals


def test_readonly_display_name_renders_without_signal():
    # Design: display-only names render from SSR initial HTML; no signal
    # subscription is needed since nothing mutates them. They must not be
    # classified as computeds either.
    fn, ui = _fn_ui("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    title = 'Hi'\n    <h1>{title}</h1>\n")
    signals, computeds, _ = compute_reactive(fn, ui)
    assert computeds == {}
    from pyweb.compiler import compile_source
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    title = 'Hi'\n    <h1>{title}</h1>\n")
    assert "Hi" in out["pages"]["H"]["html_body"]


def test_bind_target_is_signal():
    fn, ui = _fn_ui("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    q = ''\n    <input bind={q} />\n")
    signals, _, _ = compute_reactive(fn, ui)
    assert signals == ["q"]


def test_loop_var_not_signal():
    fn, ui = _fn_ui("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    todos = [1]\n    for t in todos:\n        <p>{t}</p>\n")
    signals, _, _ = compute_reactive(fn, ui)
    assert "t" not in signals


def test_python_signal_primitive():
    s = Signal(1)
    seen = []
    unsub = s.subscribe(seen.append)
    s(2)
    assert s() == 2 and seen == [2]
    unsub()
    s(3)
    assert seen == [2]


def test_python_computed_and_effect():
    calls = []
    c = Computed(lambda: 40 + 2)
    assert c() == 42
    Effect(lambda: calls.append(1))
    assert calls == [1]


def test_live_returns_resource():
    import asyncio
    r = live(lambda: [1, 2])
    assert asyncio.run(r.load()) == [1, 2]
    assert r.result == [1, 2] and not r.pending
