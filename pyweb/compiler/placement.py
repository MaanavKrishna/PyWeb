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

from .errors import CompileError as _BaseCompileError

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


class CompileError(_BaseCompileError):
    """Raised when browser-placed code violates the security boundary.

    Messages already carry ``file:line``.
    """

    def __init__(self, msg):
        ValueError.__init__(self, msg)
        self.msg, self.lineno, self.filename = msg, None, None


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
