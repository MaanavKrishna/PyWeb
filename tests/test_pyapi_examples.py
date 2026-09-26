"""Track A: pure-Python component API parity + example builds."""
from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _node_harness import extract_app_inner, run_js

REPO = Path(__file__).parent.parent


def _compile_pyapi(tree, signals: dict, handlers: dict | None = None):
    """SSR + hydrate JS for a pure-Python El tree."""
    from pyweb.compiler.codegen import js as jsgen
    from pyweb.compiler.codegen.html import SSR
    from pyweb.components import renumber

    nodes = [tree.to_ast()]
    renumber(nodes)
    analysis = types.SimpleNamespace(
        signals=set(signals), handlers=set(handlers or {}), components={}, computed=set()
    )
    ssr = SSR(analysis, dict(signals), {}, filename="api.py")
    html_body = ssr.render(nodes)
    gen = jsgen.JSGen(analysis, "api.py", "/")
    js = gen.generate(
        nodes,
        {
            "signals": {k: json.dumps(v) for k, v in signals.items()},
            "handlers": {h: {"args": [], "url": "/rpc/" + h} for h in (handlers or {})},
        },
    )
    return html_body, js


def test_pyapi_bind_onclick_expr_live():
    from pyweb import components as C

    tree = C.Page(
        C.Heading("Hello"),
        C.Input(bind=C.bind("name")),
        C.Text("hi ", C.expr("name")),
        C.Button("save", onclick=C.onclick("save")),
    )
    html_body, js = _compile_pyapi(tree, {"name": "ada"}, {"save": None})
    assert 'data-bind="name"' in html_body
    assert ">Hello<" in html_body
    case = (
        "loadSSR(document.getElementById(\"app\"), %s);\n" % json.dumps(html_body)
        + js
        + "\n;globalThis.__hook = { name };\n__fireReady();\n"
        + """
var app = document.getElementById("app");
var t0 = app.textContent;
var input = app.querySelector("input");
input.value = "grace";
input.fire("input");
var names = [];
var origFetch = null;
console.log(JSON.stringify({ t0: t0, after: app.textContent, sig: __hook.name.get() }));
"""
    )
    r = run_js(case)
    assert r.returncode == 0, r.stderr[-3000:] + "\n---JS---\n" + js[-2000:]
    got = json.loads(r.stdout.strip())
    assert "Hello" in got["t0"], got
    assert "hi ada" in got["t0"], got
    assert got["sig"] == "grace", got
    assert "hi grace" in got["after"], got


def _build_example(name: str, route: str, out: Path):
    r = subprocess.run(
        [sys.executable, "-m", "pyweb.cli", "build", f"examples/{name}/app.pyweb",
         "--out", str(out), "--route", route],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert (out / "index.html").exists()
    assert (out / "app.js").exists()
    html = (out / "index.html").read_text()
    assert '<div id="app">' in html
    inner = extract_app_inner(html)
    assert inner.strip(), "SSR body must not be empty"
    return inner, (out / "app.js").read_text()


def test_example_counter_builds_and_hydrates(tmp_path):
    inner, js = _build_example("counter", "/", tmp_path / "counter")
    assert "PyWeb.signal" in js
    case = (
        "loadSSR(document.getElementById(\"app\"), %s);\n" % json.dumps(inner)
        + js
        + "\n;globalThis.__hook = { count };\n__fireReady();\n"
        + 'console.log(JSON.stringify({ t: document.getElementById("app").textContent }));'
    )
    r = run_js(case)
    assert r.returncode == 0, r.stderr[-3000:]
    assert "0" in json.loads(r.stdout.strip())["t"]


def test_example_todo_builds(tmp_path):
    inner, js = _build_example("todo", "/todo", tmp_path / "todo")
    assert "liveList" in js
    assert "buy milk" in inner


def test_example_blog_builds(tmp_path):
    inner, js = _build_example("blog", "/blog", tmp_path / "blog")
    assert "Hello" in inner
    assert "First post" in inner
