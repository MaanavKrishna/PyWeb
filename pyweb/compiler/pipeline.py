"""Compile pipeline: .pyweb text -> {js, html, css, sourcemap, manifest}."""
from __future__ import annotations

import ast as pyast
import base64
import json
from dataclasses import dataclass, field

from pyweb.compiler.analyzer import Analyzer
from pyweb.compiler.ast import CompUse, Document, Element, Root, iter_nodes
from pyweb.compiler.codegen import css as cssgen
from pyweb.compiler.codegen import html as htmlgen
from pyweb.compiler.codegen import js as jsgen
from pyweb.compiler.codegen.sourcemap import SourceMap
from pyweb.compiler.parser import parse_text
from pyweb.compiler.placement import Placement
from pyweb.compiler.rpc import rpc_manifest


@dataclass
class Artifacts:
    route: str
    js: str
    html: str
    css: str
    css_file: str | None
    sourcemap: dict
    manifest: dict
    live: bool = False

    def js_with_map(self) -> str:
        raw = json.dumps(self.sourcemap).encode()
        b64 = base64.b64encode(raw).decode()
        hook = (
            "\nwindow.__pyweb_sourcemap = %s;"
            "\nwindow.__pyweb_error = PyWeb.makeErrorHook(window.__pyweb_sourcemap);"
            "\n//# sourceMappingURL=data:application/json;base64,%s\n"
            % (json.dumps(self.sourcemap), b64)
        )
        return self.js + hook


def _initial_values(python_src: str) -> tuple[dict, dict, dict]:
    """Execute module-level signal/computed initializers safely-ish.

    Only supports literal / list / dict / boolean / numeric initializers;
    anything else falls back to a documented placeholder.
    """
    values: dict = {}
    boot_signals: dict = {}
    boot_computed: dict = {}
    boot_line = 0
    try:
        tree = pyast.parse(python_src)
    except SyntaxError:
        return values, boot_signals, boot_computed
    from pyweb.reactive import Signal, computed as _computed, signal as _signal

    safe_globals = {"signal": _signal, "computed": _computed, "True": True, "False": False, "None": None}

    def _unwrap(made):
        if isinstance(made, Signal):
            return made.get()
        if hasattr(made, "get"):
            try:
                return made.get()
            except Exception:
                return None
        return made

    def _record(target: str, call: pyast.Call) -> None:
        if target in values:
            return
        try:
            src = pyast.unparse(call)
            made = eval(src, {"__builtins__": {}}, dict(safe_globals))  # noqa: S307
            plain = _unwrap(made)
            values[target] = plain
            if getattr(call.func, "id", "") == "signal":
                boot_signals[target] = _js_literal(plain)
            else:
                boot_computed[target] = "null"
        except Exception:
            values[target] = _Placeholder(target)
            boot_signals[target] = "null"

    for node in pyast.walk(tree):
        if isinstance(node, pyast.Assign) and isinstance(node.value, pyast.Call):
            if getattr(node.value.func, "id", "") in ("signal", "computed"):
                for t in node.targets:
                    if isinstance(t, pyast.Name):
                        _record(t.id, node.value)
                        boot_line = max(boot_line, getattr(node.value, "lineno", boot_line))
        elif isinstance(node, pyast.AnnAssign) and isinstance(node.value, pyast.Call):
            if getattr(node.value.func, "id", "") in ("signal", "computed"):
                if isinstance(node.target, pyast.Name):
                    _record(node.target.id, node.value)
                    boot_line = max(boot_line, getattr(node.value, "lineno", boot_line))
    return values, {"signals": boot_signals, "computed": boot_computed, "line": boot_line}, values


class _Placeholder:
    def __init__(self, name: str):
        self.name = name

    def get(self):
        return None


def _js_literal(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_js_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        return "({" + ", ".join(f"{json.dumps(str(k))}: {_js_literal(v)}" for k, v in value.items()) + "})"
    return "null"


def build_text(source: str, filename: str = "<input>", route: str = "/") -> Artifacts:
    doc: Document = parse_text(source, filename)
    analysis = Analyzer(doc).run()
    # placement marks live subtrees
    place = Placement(set(analysis.signals), set(analysis.handlers), analysis.components)
    live = False
    templates: dict[str, list] = {}
    for root in doc.roots:
        if root.kind == "component":
            templates[root.name] = root.nodes
    for root in doc.roots:
        if place.run(root.nodes):
            live = True
    values, boot, _ = _initial_values(doc.python_src)
    # handlers manifest
    from pyweb.compiler.rpc import collect_handlers

    handlers, sigs = collect_handlers(doc.python_src)
    boot["handlers"] = rpc_manifest(handlers, sigs)
    # pick page root for route (first page; route override via filename)
    root = next((r for r in doc.roots if r.kind == "page"), doc.roots[0] if doc.roots else None)
    # CSS extraction
    css_text, _mapping = cssgen.extract(root.nodes if root else [], doc.styles, route)
    css_file = cssgen.bundle_name(css_text) if css_text.strip() else None
    # JS
    sm = SourceMap(source=filename)
    gen = jsgen.JSGen(analysis, filename, route)
    gen.map = sm
    js = gen.generate(root.nodes if root else [], boot)
    sourcemap = sm.to_dict("app.js")
    # SSR html
    ssr = htmlgen.SSR(analysis, values, templates, filename)
    body = ssr.render(root.nodes if root else [])
    title = root.name if root else "PyWeb"
    html = ssr.page(title, body, "app.js", css_file)
    manifest = {
        "route": route,
        "root": root.name if root else None,
        "routes": analysis.routes,
        "rpc": boot["handlers"],
        "css": css_file,
        "live": live,
    }
    return Artifacts(
        route=route,
        js=js,
        html=html,
        css=css_text,
        css_file=css_file,
        sourcemap=sourcemap,
        manifest=manifest,
        live=live,
    )


def build_file(path: str, route: str | None = None) -> Artifacts:
    with open(path, encoding="utf-8") as f:
        source = f.read()
    return build_text(source, filename=path, route=route or "/")
