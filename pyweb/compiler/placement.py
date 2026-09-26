"""PyWeb compiler placement: decide browser-vs-server execution.

Security boundary: secrets and server-only imports must NEVER enter
browser bundles. :func:`check_source` enforces this — referencing a
``@server`` secret/credential or a server-only import (databases, secret
environment reads, direct file access) from browser-placed code raises
:class:`CompileError` naming the ``file:line`` and the leak path.
"""
import ast
import re

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


def _is_server_only_import(node: ast.AST) -> str | None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top in SERVER_ONLY_MODULES:
                return alias.name
    elif isinstance(node, ast.ImportFrom):
        top = (node.module or "").split(".")[0]
        if top in SERVER_ONLY_MODULES:
            return node.module or ""
        if top == "os":
            names = {a.name for a in node.names}
            if names & {"environ", "getenv", "popen", "system"}:
                return node.module or ""
    return None


def _leak_path(source_line: str) -> str | None:
    for pattern in SECRET_PATTERNS:
        match = pattern.search(source_line)
        if match:
            return match.group(0)
    return None


def check_source(source: str, filename: str = "<unknown>",
                 placement: str = "server") -> str:
    """Check ``source`` against the security boundary for ``placement``.

    Returns the placement unchanged when clean. Raises :class:`CompileError`
    with ``filename:line`` and the leak path when browser-placed code
    references secrets or server-only imports. Stable API: ``(source,
    filename, placement) -> placement``.
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
                lineno = getattr(node, "lineno", 1)
                raise CompileError(
                    f"{filename}:{lineno}: server-only import {mod!r} "
                    f"leaks into browser bundle via {filename} "
                    f"(leak path: import {mod})")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in server_names:
            lineno = getattr(node, "lineno", 1)
            line = lines[lineno - 1] if 0 < lineno <= len(lines) else node.id
            raise CompileError(
                f"{filename}:{lineno}: @server symbol {node.id!r} "
                f"referenced from browser code (leak path: "
                f"{line.strip()[:120]})")
    for i, line in enumerate(lines, start=1):
        leak = _leak_path(line)
        if leak:
            raise CompileError(
                f"{filename}:{i}: secret/credential reference "
                f"{leak!r} leaks into browser bundle via {filename} "
                f"(leak path: {line.strip()[:120]})")
    return placement
