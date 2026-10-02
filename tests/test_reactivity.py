"""Reactivity classification (signal / computed / const) and Python primitives."""

from pyweb import Computed, Effect, Signal, live
from pyweb.compiler import compile_source

HEAD = "from pyweb import App\napp=App()\n@app.page('/')\n"


def page(body):
    return compile_source(HEAD + body)["pages"]["H"]


def test_counter_signal():
    p = page("def H():\n    count = 0\n    def inc():\n        count += 1\n    <button onclick={inc}>{count}</button>\n")
    assert p["signals"] == ["count"] and not p["computeds"]
    assert ("count", "__dom__") in p["edges"]


def test_computed_derivation():
    p = page("def H():\n    price = 100\n    quantity = 2\n    total = price * quantity\n"
             "    <input bind={quantity} />\n    <p>{total}</p>\n")
    assert p["computeds"]["total"]["deps"] == ["price", "quantity"]
    assert ("quantity", "total") in p["edges"] and ("total", "__dom__") in p["edges"]
    assert "total" not in p["signals"]


def test_constants_are_not_reactive():
    p = page("def H():\n    title = 'Hi'\n    <h1>{title}</h1>\n")
    assert p["signals"] == [] and p["computeds"] == {} and p["js"] == ""
    assert "<h1>Hi</h1>" in p["html_body"]


def test_bind_target_is_signal():
    p = page("def H():\n    q = ''\n    <input bind={q} />\n")
    assert p["signals"] == ["q"]


def test_method_mutation_makes_a_signal():
    p = page("def H():\n    todos = []\n    def add():\n        todos.append(1)\n    <ul onclick={add}>{len(todos)}</ul>\n")
    assert p["signals"] == ["todos"]
    assert "append() in add()" in p["placement"]["todos"][1]


def test_loop_var_not_signal():
    p = page("def H():\n    todos = [1]\n    for t in todos:\n        <p>{t}</p>\n")
    assert "t" not in p["signals"]


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
