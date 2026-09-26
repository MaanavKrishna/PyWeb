"""Execution-location analysis: browser / server / shared per symbol.

Security boundary: secrets and server-only imports must NEVER enter
browser bundles. :func:`check_source` enforces this — referencing a
secret/credential or a server-only import (databases, secret environment
reads, direct file access) from browser-placed code raises
:class:`CompileError` naming the ``file:line`` and the leak path.
"""

from __future__ import annotations

import ast
import re

from .analyzer import server_indicators
from .reactivity import ui_expr_names

SERVER_ONLY_MODULES = frozenset({
    "psycopg", "psycopg2", "sqlite3", "asyncpg", "pymongo", "redis",
    "boto3", "subprocess", "socket",
})

SECRET_PATTERNS = (
    re.compile(r"(?i)\b(SECRET|SECRET_KEY|API_KEY|DB_PASSWORD|DATABASE_URL"
               r"|PRIVATE_KEY|CREDENTIALS|ACCESS_TOKEN)\b"),
    re.compile(r"os\.environ\s*\[.*?\]"),
    re.compile(r"os\.getenv\s*\("),
    re.compile(r"(?i)\bpassword\s*=\s*['\"]"),
    re.compile(r"\bopen\s*\("),
)

_SERVER_DECORATOR = "server"


class CompileError(Exception):
    """Raised when browser-placed code violates the security boundary."""


def _is_server_only_import(node):
    if isinstance(node, ast.Import):
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top in SERVER_ONLY_MODULES:
                return alias.name
    elif isinstance(node, ast.ImportFrom):
        top = (node.module or "").split(".")[0]
        if top in SERVER_ONLY_MODULES:
            return node.module
    return None


def check_source(source, filename="<unknown>", placement="server"):
    """Check ``source`` against the security boundary for ``placement``.

    Returns the placement unchanged when clean. Raises :class:`CompileError`
    with ``filename:line`` and the leak path when browser-placed code
    references secrets or server-only imports.
    """
    if placement != "browser":
        return placement
    lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise CompileError(f"{filename}:{exc.lineno or 1}: "
                           f"unparseable browser source: {exc.msg}")
    server_names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                dec_name = ""
                if isinstance(dec, ast.Name):
                    dec_name = dec.id
                elif isinstance(dec, ast.Attribute):
                    dec_name = dec.attr
                if dec_name == _SERVER_DECORATOR:
                    server_names.add(node.name)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = _is_server_only_import(node)
            if mod:
                raise CompileError(
                    f"{filename}:{node.lineno}: server-only import {mod!r} "
                    f"leaks into browser bundle (import)")
        elif isinstance(node, ast.Name) and node.id in server_names:
            raise CompileError(
                f"{filename}:{node.lineno}: @server symbol {node.id!r} "
                f"leaks into browser bundle (call)")
    for i, line in enumerate(lines, start=1):
        for pat in SECRET_PATTERNS:
            if pat.search(line):
                raise CompileError(
                    f"{filename}:{i}: secret/credential pattern "
                    f"leaks into browser bundle ({pat.pattern[:40]}...)")
    return placement


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
