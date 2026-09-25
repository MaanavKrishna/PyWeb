"""RPC specs, placement decisions, server validation/dispatch."""

import ast
import json

from pyweb import browser, server, worker
from pyweb.compiler import parser as P
from pyweb.compiler import compile_source
from pyweb.compiler.placement import place_page
from pyweb.compiler.reactivity import compute_reactive
from pyweb.compiler.rpc import client_stub, rpc_specs
from pyweb.runtime.server import Request, Server


def test_rpc_spec_types():
    tree, _, _ = P.parse_source("from pyweb import server\n@server\ndef f(name: str, age: int) -> bool:\n    return True\n")
    specs = rpc_specs(tree)
    assert specs[0]["args"] == [{"name": "name", "type": "str"}, {"name": "age", "type": "int"}]
    assert specs[0]["returns"] == "bool" and specs[0]["location"] == "server"


def test_worker_location_and_stub():
    tree, _, _ = P.parse_source("from pyweb import worker\n@worker\ndef heavy(x: int) -> int:\n    return x\n")
    specs = rpc_specs(tree)
    assert specs[0]["location"] == "worker"
    assert "rpc('heavy'" in client_stub(specs[0])


def test_non_rpc_fn_ignored():
    tree, _, _ = P.parse_source("def plain():\n    pass\n")
    assert rpc_specs(tree) == []


def test_explicit_browser_override():
    tree, _, _ = P.parse_source("from pyweb import browser\n@browser\ndef h():\n    pass\n")
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
    tree2, ui_all, _ = P.parse_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <p>x</p>\n")
    fn2 = next(n for n in tree2.body if isinstance(n, ast.FunctionDef))
    from pyweb.compiler.placement import explicit_location
    assert explicit_location(fn) == ("browser", "explicit @browser marker")


def test_server_dep_goes_to_server():
    src = "import psycopg\nfrom pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    rows = psycopg.connect('x').execute('q')\n    <p>{rows}</p>\n"
    out = compile_source(src)
    assert out["pages"]["H"]["placement"]["__page__"][0] == "browser+server"


def test_decorator_metadata():
    @server
    def f():
        pass
    @browser
    def g():
        pass
    assert f.__pyweb_location__ == "server" and g.__pyweb_location__ == "browser"


def test_rpc_validation_ok_and_errors():
    out = compile_source("from pyweb import server\n@server\ndef add(a: int, b: int) -> int:\n    return a\n")
    s = Server(out)

    def add(a: int, b: int) -> int:
        return a + b
    s.register_rpc(add)
    ok = s.handle(Request("POST", "/__pyweb/rpc/add", body=json.dumps({"args": {"a": 2, "b": 3}}).encode()))
    assert json.loads(ok.body)["result"] == 5
    bad = s.handle(Request("POST", "/__pyweb/rpc/add", body=json.dumps({"args": {"a": "x", "b": 1}}).encode()))
    assert bad.status == 422
    missing = s.handle(Request("POST", "/__pyweb/rpc/add", body=json.dumps({"args": {"a": 1}}).encode()))
    assert missing.status == 422


def test_rpc_unknown_and_bad_json():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    <p>x</p>\n")
    s = Server(out)
    assert s.handle(Request("POST", "/__pyweb/rpc/nope", body=b"{}")).status == 404
    assert s.handle(Request("POST", "/__pyweb/rpc/nope", body=b"{{{")).status in (400, 404)


def test_rpc_email_validation():
    out = compile_source("from pyweb import server\n@server\ndef f(e: Email) -> str:\n    return e\n")
    s = Server(out)

    def f(e: "Email") -> str:
        return e
    s.register_rpc(f)
    bad = s.handle(Request("POST", "/__pyweb/rpc/f", body=json.dumps({"args": {"e": "nope"}}).encode()))
    assert bad.status == 422


def test_route_param_coercion_for_pages():
    out = compile_source("from pyweb import App\napp=App()\n@app.page('/u/{user_id}')\ndef U(user_id):\n    <p>{user_id}</p>\n")
    s = Server(out)
    assert s.handle(Request("GET", "/u/42")).status == 200
    assert s.handle(Request("GET", "/nope")).status == 404
