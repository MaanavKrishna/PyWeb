"""Prototype tests: reactivity (H1), RPC (H3), SSR/placement/security."""

from pyweb.compiler import compile_source
from pyweb.runtime.server import Request, Server

COUNTER = open("examples/counter/app.pyweb").read()
TODO = open("examples/todo/app.pyweb").read()


def test_counter_reactive_count():
    out = compile_source(COUNTER, filename="app.pyweb")
    page = out["pages"]["Home"]
    assert page["signals"] == ["count"], page["signals"]
    assert page["placement"]["count"][0] == "browser"
    assert page["placement"]["increment"][0] == "browser"
    assert "Count:" in page["html"] and "Counter" in page["html"]
    assert "sig(" in page["js"] and "increment" in page["js"]
    assert "Count: {count}" not in page["html"]


def test_todo_rpc_and_ssr():
    out = compile_source(TODO, filename="app.pyweb")
    assert any(r["name"] == "get_count" for r in out["rpc"])
    spec = [r for r in out["rpc"] if r["name"] == "get_count"][0]
    assert spec["returns"] == "int"
    page = out["pages"]["Home"]
    assert "Todos" in page["html"] and "buy milk" in page["html"]
    assert "get_count" in page["js"] and "/__pyweb/rpc/" in open("pyweb/runtime/browser/runtime.js").read()


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
        "from pyweb import App\napp = App()\n@app.page('/')\ndef Shop():\n    price = 100\n    quantity = 2\n    total = price * quantity\n    <p>{total}</p>\n",
        filename="s.pyweb",
    )
    page = out["pages"]["Shop"]
    assert page["computeds"]["total"]["deps"] == ["price", "quantity"]
    assert ("price", "total") in out["graph"].edges and ("total", "__dom__") in out["graph"].edges
    static = compile_source(
        "from pyweb import App\napp = App()\n@app.page('/')\ndef H():\n    <h1>Hello</h1>\n",
        filename="h.pyweb",
    )
    assert "runtime.js" not in static["pages"]["H"]["js"]
    assert "<h1>Hello</h1>" in static["pages"]["H"]["html"]


def test_secret_never_goes_to_browser():
    try:
        compile_source(
            "from pyweb import App\napp = App()\n@app.page('/')\ndef H():\n    API_TOKEN = 'x'\n    <p>{API_TOKEN}</p>\n",
            filename="x.pyweb",
        )
    except ValueError as exc:
        assert "server secret" in str(exc)
    else:
        raise AssertionError("secret leak not rejected")
