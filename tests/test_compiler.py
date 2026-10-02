"""Compiler: reactivity, RPC, SSR, placement and the secret boundary."""

from pathlib import Path

from pyweb.compiler import compile_source
from pyweb.runtime.server import Request, Server

COUNTER = Path("examples/counter/app.pyweb").read_text()
TODO = Path("examples/todo/app.pyweb").read_text()


def test_counter_reactive_count():
    out = compile_source(COUNTER, filename="app.pyweb")
    page = out["pages"]["Home"]
    assert page["signals"] == ["count", "step"], page["signals"]
    assert page["computeds"]["doubled"]["deps"] == ["count"]
    assert page["placement"]["count"][0] == "browser"
    assert page["placement"]["increment"][0] == "browser"
    assert "Count: 0" in page["html"] and "Counter" in page["html"]
    assert "$signal(" in page["js"] and "function increment()" in page["js"]
    assert "Count: {count}" not in page["html"]


def test_server_call_from_handler_becomes_awaited_rpc():
    src = ("from pyweb import App, server\napp = App()\n@server\ndef add(a: int, b: int) -> int:\n"
           "    return a + b\n@app.page('/')\ndef H():\n    total = 0\n"
           "    def go():\n        total = add(total, 2)\n    <button onclick={go}>{total}</button>\n")
    out = compile_source(src, filename="app.pyweb")
    js = out["pages"]["H"]["js"]
    assert "async function go()" in js
    assert 'total((await $rpc("add", {"a": total(), "b": 2})));' in js
    spec = out["rpc"][0]
    assert spec["name"] == "add" and spec["returns"] == "int"
    assert out["pages"]["H"]["placement"]["go"][0] == "browser"


def test_server_only_names_cannot_reach_the_browser():
    import pytest
    from pyweb.compiler.errors import CompileError
    src = ("import sqlite3\nfrom pyweb import App\napp = App()\n@app.page('/')\ndef H():\n"
           "    n = 0\n    def go():\n        sqlite3.connect('x')\n    <button onclick={go}>{n}</button>\n")
    with pytest.raises(CompileError) as e:
        compile_source(src, filename="app.pyweb")
    assert "app.pyweb:8" in str(e.value) and "only exists on the server" in str(e.value)


def test_rpc_validation_and_dispatch():
    out = compile_source(
        "from pyweb import server\n\n@server\ndef create_user(name: str, age: int) -> str:\n    return name\n",
        filename="t.pyweb",
    )
    server = Server(out)

    def create_user(name: str, age: int) -> str:
        return f"{name}:{age}"

    server.register_rpc(create_user)
    ok = server.handle(Request("POST", "/__pyweb/rpc/create_user", body=b'{"args": {"name": "a", "age": 3}}'))
    assert ok.status == 200 and '"a:3"' in ok.body
    bad = server.handle(Request("POST", "/__pyweb/rpc/create_user", body=b'{"args": {"name": "a", "age": "x"}}'))
    assert bad.status == 422
    missing = server.handle(Request("GET", "/nope"))
    assert missing.status == 404


def test_computed_graph_and_static_page():
    out = compile_source(
        "from pyweb import App\napp = App()\n@app.page('/')\ndef Shop():\n    price = 100\n    quantity = 2\n"
        "    total = price * quantity\n    <input bind={quantity} />\n    <p>{total}</p>\n",
        filename="s.pyweb",
    )
    page = out["pages"]["Shop"]
    assert page["computeds"]["total"]["deps"] == ["price", "quantity"]
    assert ("quantity", "total") in out["graph"].edges and ("total", "__dom__") in out["graph"].edges
    static = compile_source(
        "from pyweb import App\napp = App()\n@app.page('/')\ndef H():\n    <h1>Hello</h1>\n",
        filename="h.pyweb",
    )
    assert static["pages"]["H"]["js"] == ""
    assert "<h1>Hello</h1>" in static["pages"]["H"]["html"] and "<script" not in static["pages"]["H"]["html"]


def test_secret_never_goes_to_browser():
    import pytest
    with pytest.raises(ValueError) as e:
        compile_source(
            "from pyweb import App\napp = App()\n@app.page('/')\ndef H():\n    API_TOKEN = 'x'\n    <p>{API_TOKEN}</p>\n",
            filename="x.pyweb",
        )
    assert "server secret 'API_TOKEN'" in str(e.value) and "x.pyweb:5" in str(e.value)


def test_secret_from_server_is_rejected_even_if_not_literal():
    import pytest
    src = ("import os\nfrom pyweb import App\napp = App()\n@app.page('/')\ndef H():\n"
           "    db_password = os.environ['DB_PASSWORD']\n    <p>{db_password}</p>\n")
    with pytest.raises(ValueError):
        compile_source(src, filename="x.pyweb")


def test_server_values_stay_private_unless_read():
    src = ("from pyweb import App\napp = App()\ndef load():\n    return {'name': 'a', 'hash': 'x'}\n"
           "@app.page('/')\ndef H():\n    user = load()\n    name = user['name']\n    n = 0\n"
           "    def inc():\n        n += 1\n    <p onclick={inc}>{name} {n}</p>\n")
    out = compile_source(src)
    page = out["pages"]["H"]
    assert page["state_keys"] == ["name", "n"]            # `user` (with the hash) is never sent
    assert page["placement"]["user"][0] == "server"
