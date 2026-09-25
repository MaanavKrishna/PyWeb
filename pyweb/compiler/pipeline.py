"""Top-level pipeline: source → AppGraph + HTML/JS artifacts."""

from __future__ import annotations

import ast

from . import parser as P
from .analyzer import func_info
from .codegen.emit_html import emit_html, emit_page
from .codegen.emit_js import emit_js
from .codegen.ir import build_graph, to_text
from .placement import place_page
from .reactivity import compute_reactive
from .rpc import rpc_specs


def _handler_lowers(func_node, signals):
    """Lower page-level nested event handlers (e.g. `count += 1`) to JS bodies."""
    from .codegen.emit_js import _py2js
    from .reactivity import handler_names as _hn
    # UI association happens per-page in the caller; here lower all nested fns.
    out = {}
    nested = [n for n in func_node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for fn in nested:
        stmts = []
        for stmt in fn.body:
            try:
                code = ast.unparse(stmt) if hasattr(ast, "unparse") else ""
            except Exception:  # noqa: BLE001
                continue
            if "__pyweb_ui__" in code:
                continue
            stmts.append(code)
        out[fn.name] = {"body_js": "; ".join(s for s in stmts if s) or "",
                        "line": fn.lineno, "signals": list(signals)}
        out[fn.name]["body_js"] = _py2js(out[fn.name]["body_js"], signals)
    return out


def _initial_values(func_node):
    vals = {}
    for n in func_node.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            try:
                vals[n.targets[0].id] = ast.literal_eval(n.value)
            except Exception:
                pass
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
            try:
                vals[n.target.id] = ast.literal_eval(n.value)
            except Exception:
                pass
    return vals


def compile_source(source, filename="<pyweb>", route="/", title="PyWeb"):
    tree, ui_all, pages = P.parse_source(source, filename)
    rpc = rpc_specs(tree)
    # Find page functions (@app.page or all top-level fns with UI).
    page_nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decos = [ast.unparse(d) if hasattr(ast, "unparse") else "" for d in node.decorator_list]
            if any("page" in d for d in decos) or node.name == "Home":
                page_nodes.append(node)
    if not page_nodes:
        page_nodes = [n for n, _ in pages if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))][:1]
    artifacts = {}
    all_signals, all_computeds, all_place, all_edges = {}, {}, {}, []
    page_infos = []
    for fn in page_nodes:
        span = (fn.lineno, getattr(fn, "end_lineno", fn.lineno))
        ui = [n for n in ui_all if span[0] <= getattr(n, "line", 0) <= span[1]]
        signals, computeds, edges = compute_reactive(fn, ui)
        placement = place_page(tree, fn, ui, signals, computeds)
        initial = _initial_values(fn)
        params = [a.arg for a in fn.args.args]
        route_of = route
        for d in fn.decorator_list:
            txt = ast.unparse(d) if hasattr(ast, "unparse") else ""
            if "page" in txt:
                import re
                m = re.search(r"""['\"]([^'\"]+)['\"]""", txt)
                if m:
                    route_of = m.group(1)
        handlers = _handler_lowers(fn, signals)
        smap: list = []
        js = emit_js(fn.name, ui, signals, computeds, initial, rpc, handlers=handlers, sourcemap_out=smap)
        html_body = emit_html(ui, initial)
        html = emit_page(route_of, title, ui, initial, js_url=f"/static/{fn.name}.js")
        artifacts[fn.name] = {"ui": ui, "signals": signals, "computeds": computeds,
                              "placement": placement, "edges": edges, "initial": initial,
                              "js": js, "html": html, "html_body": html_body,
                              "lineno": fn.lineno, "params": params, "route": route_of,
                              "sourcemap": smap, "handlers": handlers}
        all_signals.update({s: initial.get(s) for s in signals})
        all_computeds.update(computeds)
        all_place.update(placement)
        all_edges.extend(edges)
        page_infos.append({"name": fn.name, "route": route_of, "signals": signals})
    # Security gate: a secret-like browser signal only leaks when its initial
    # value is a non-empty literal baked into the SSR/JS bundle. Empty form
    # state (e.g. `password = ""` bound to an <input>) originates in the
    # browser and is safe.
    for s, v in all_signals.items():
        if v not in (None, "", 0, False) and any(
            h in s.upper() for h in ("SECRET", "PASSWORD", "API_KEY", "TOKEN", "PRIVATE")
        ):
            raise ValueError(f"ERROR: server secret {s!r} referenced from browser-executed code")
    graph = build_graph(page_infos, rpc, all_signals, all_computeds, all_place, all_edges)
    return {"graph": graph, "ir_text": to_text(graph), "rpc": rpc, "pages": artifacts}
