"""Execution-location analysis: browser / server / shared per symbol."""

from __future__ import annotations

import ast

from .analyzer import server_indicators
from .reactivity import ui_expr_names


def explicit_location(node):
    for d in node.decorator_list:
        name = ast.unparse(d) if hasattr(ast, "unparse") else ""
        for loc in ("browser", "server", "edge", "worker", "shared"):
            if loc in name:
                return loc, f"explicit @{loc} marker"
    return None, ""


def place_page(tree, func_node, ui, signals, computeds):
    """Return {symbol: (location, reason)} for page locals + helpers."""
    decisions = {}
    ui_names, _ = ui_expr_names(ui)
    server_reasons = server_indicators(tree, func_node)
    server_locked = bool(server_reasons)
    nested = {n.name: n for n in func_node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for name, fn in nested.items():
        loc, why = explicit_location(fn)
        if loc:
            decisions[name] = (loc, why)
        elif server_indicators(tree, fn):
            decisions[name] = ("server", "; ".join(server_indicators(tree, fn)))
        elif name in ui_names or any(
            (ast.unparse(h) if hasattr(ast, "unparse") else "") for h in []
        ):
            decisions[name] = ("browser", "event handler reachable from UI, browser-safe deps")
        else:
            # referenced from markup attrs?
            decisions[name] = ("browser", "referenced by UI markup, pure/browser-safe")
    for s in signals:
        if server_locked and s not in ui_names:
            decisions[s] = ("server", "; ".join(server_reasons))
        else:
            decisions[s] = ("browser", "reactive UI state read by markup, mutated by events")
    for name in computeds:
        decisions[name] = ("browser", f"pure derivation of {computeds[name]['deps']}, all inputs local")
    # Server functions at module level handled in rpc.py; pages default:
    decisions.setdefault("__page__", ("browser+server", "SSR initial HTML on server, activation in browser"))
    return decisions
