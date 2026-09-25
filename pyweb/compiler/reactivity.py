"""Reactivity lowering: plain locals → Signal / Computed.

A local is reactive iff used in rendered UI (or a computed) AND mutated
after init / bound / derived. Derived aliases (`total = price * quantity`)
become Computed nodes with dependency edges for the fine-grained graph.
"""

from __future__ import annotations

import ast

from .analyzer import func_info


def ui_expr_names(ui):
    names = set()
    bind_targets = set()

    def walk(nodes):
        for n in nodes:
            t = type(n).__name__
            if t == "ExprNode":
                try:
                    tree = ast.parse(n.code, mode="eval")
                    names.update(x.id for x in ast.walk(tree) if isinstance(x, ast.Name))
                except SyntaxError:
                    pass
            elif t == "Element":
                for key, val in n.attrs.items():
                    if isinstance(val, tuple) and val[0] == "expr":
                        if key in ("bind",):
                            bind_targets.add(val[1])
                            names.add(val[1])
                        else:
                            try:
                                tree = ast.parse(val[1], mode="eval")
                                names.update(x.id for x in ast.walk(tree) if isinstance(x, ast.Name))
                            except SyntaxError:
                                pass
                    elif key in ("onclick", "oninput", "onchange") and isinstance(val, tuple):
                        try:
                            tree = ast.parse(val[1], mode="eval")
                            names.update(x.id for x in ast.walk(tree) if isinstance(x, ast.Name))
                        except SyntaxError:
                            pass
                walk(n.children)
            elif t in ("ControlFor", "ControlIf"):
                for attr in ("iterable", "test", "target"):
                    code = getattr(n, attr, "")
                    if code:
                        try:
                            tree = ast.parse(code, mode="eval")
                            names.update(x.id for x in ast.walk(tree) if isinstance(x, ast.Name))
                        except SyntaxError:
                            names.add(code) if attr == "target" else None
                walk(n.body)
                walk(getattr(n, "orelse", []))

    walk(ui)
    return names, bind_targets


def assigned_names(func_node):
    out = set()
    for n in ast.walk(func_node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
            out.add(n.target.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            out.add(n.target.id)
    return out


def mutated_in_handlers(func_node, handler_names):
    mutated = set()
    fns = {n.name: n for n in ast.walk(func_node) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for h in handler_names:
        if h in fns:
            mutated |= assigned_names(fns[h])
    return mutated


def handler_names(ui):
    names = set()

    def walk(nodes):
        for n in nodes:
            if type(n).__name__ == "Element":
                for key, val in n.attrs.items():
                    if key.startswith("on") and isinstance(val, tuple):
                        names.add(val[1].split("(")[0])
                walk(n.children)
            elif type(n).__name__ in ("ControlFor", "ControlIf"):
                walk(n.body)
                walk(getattr(n, "orelse", []))

    walk(ui)
    return names


def compute_reactive(func_node, ui):
    """Return (signals, computeds, graph_edges).

    Pure derivations (`total = price * quantity`) become Computed nodes;
    only genuinely mutated/bound names become Signals.
    """
    ui_names, bind_targets = ui_expr_names(ui)
    assigned = assigned_names(func_node)
    handlers = handler_names(ui)
    handler_mutated = mutated_in_handlers(func_node, handlers)
    aug = set()
    loop_vars = set()
    for n in ast.walk(func_node):
        if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
            aug.add(n.target.id)
        if isinstance(n, ast.For):
            t = n.target
            if isinstance(t, ast.Name):
                loop_vars.add(t.id)
            else:
                loop_vars.update(x.id for x in ast.walk(t) if isinstance(x, ast.Name))
    single = {}
    for n in func_node.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            name = n.targets[0].id
            if name in single:
                single[name] = None
            else:
                deps = {x.id for x in ast.walk(n.value) if isinstance(x, ast.Name)} - {name}
                single[name] = (n.value, deps, n.lineno)
    computeds = {}
    for name, spec in single.items():
        if not spec:
            continue
        value, deps, lineno = spec
        if (name in ui_names and deps and name not in bind_targets
                and name not in handler_mutated and name not in aug):
            try:
                code = ast.unparse(value) if hasattr(ast, "unparse") else name
            except Exception:  # noqa: BLE001
                code = name
            computeds[name] = {"code": code, "deps": sorted(deps), "line": lineno}
    params = _params(func_node)
    stateful = handler_mutated | bind_targets | aug | ((assigned & ui_names) - params - loop_vars)
    signals = sorted(((ui_names & assigned) - set(computeds)) & stateful)
    edges = []
    for s in signals:
        edges.append((s, "__dom__"))
    for name, c in computeds.items():
        for d in c["deps"]:
            edges.append((d, name))
        edges.append((name, "__dom__"))
    return signals, computeds, edges


def _params(func_node):
    return {a.arg for a in list(func_node.args.args) + list(func_node.args.kwonlyargs)}
