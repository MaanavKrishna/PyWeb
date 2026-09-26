"""RPC bridge: page-level Python functions become fetch-callable endpoints."""
from __future__ import annotations

import ast as pyast


def collect_handlers(python_src: str) -> tuple[set[str], dict[str, list[str]]]:
    """Return (handler_names, signatures) for defs in the page module.

    Includes nested defs (handlers are usually defined inside @page bodies).
    """
    try:
        tree = pyast.parse(python_src)
    except SyntaxError:
        return set(), {}
    names: set[str] = set()
    sigs: dict[str, list[str]] = {}
    skip: set[str] = set()
    for node in tree.body:
        if isinstance(node, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
            for deco in node.decorator_list:
                fname = ""
                if isinstance(deco, pyast.Call):
                    fname = getattr(deco.func, "id", "")
                elif isinstance(deco, pyast.Name):
                    fname = deco.id
                if fname in ("page", "component"):
                    skip.add(node.name)
    for node in pyast.walk(tree):
        if isinstance(node, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
            if node.name.startswith("_") or node.name in skip:
                continue
            names.add(node.name)
            sigs[node.name] = [a.arg for a in node.args.args if a.arg not in ("self", "cls")]
    return names, sigs


def rpc_manifest(handlers: set[str], sigs: dict[str, list[str]]) -> dict:
    return {
        name: {"args": sigs.get(name, []), "url": f"/api/{name}", "method": "POST"}
        for name in sorted(handlers)
    }
