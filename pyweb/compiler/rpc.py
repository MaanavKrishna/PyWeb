"""Automatic RPC: @server/@worker/@edge functions → endpoints + stubs."""

from __future__ import annotations

import ast


def is_rpc(node):
    for d in node.decorator_list:
        name = ast.unparse(d) if hasattr(ast, "unparse") else ""
        if any(k in name for k in ("server", "worker", "edge", "task")):
            return True
    return False


def rpc_specs(tree):
    specs = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and is_rpc(node):
            args = []
            for a in node.args.args:
                ann = ast.unparse(a.annotation) if (hasattr(ast, "unparse") and a.annotation) else "Any"
                args.append({"name": a.arg, "type": ann})
            ret = ast.unparse(node.returns) if (hasattr(ast, "unparse") and node.returns) else "Any"
            loc = "server"
            for d in node.decorator_list:
                name = ast.unparse(d) if hasattr(ast, "unparse") else ""
                if "worker" in name:
                    loc = "worker"
                elif "edge" in name:
                    loc = "edge"
            from .lower import is_generator
            specs.append({"name": node.name, "args": args, "returns": ret,
                          "location": loc, "line": node.lineno,
                          "async": isinstance(node, ast.AsyncFunctionDef), "stream": is_generator(node)})
    return specs
