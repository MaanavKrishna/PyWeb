"""Effect/security analysis: read/write sets, server-only deps, secrets."""

from __future__ import annotations

import ast

SERVER_MODULES = {"psycopg", "psycopg2", "sqlalchemy", "sqlite3", "os", "subprocess", "socket"}
SECRET_HINTS = ("SECRET", "PASSWORD", "API_KEY", "TOKEN", "PRIVATE")


class FuncInfo:
    def __init__(self, node):
        self.node = node
        self.reads: set[str] = set()
        self.writes: set[str] = set()
        self.calls: set[str] = set()
        self.awaited_server = False


class ReadWrite(ast.NodeVisitor):
    def __init__(self):
        self.reads, self.writes, self.calls = set(), set(), set()

    def visit_Name(self, node):
        (self.writes if isinstance(node.ctx, (ast.Store,)) else self.reads).add(node.id)

    def visit_AugAssign(self, node):
        self.generic_visit(node)
        if isinstance(node.target, ast.Name):
            self.reads.add(node.target.id)
            self.writes.add(node.target.id)

    def visit_Call(self, node):
        f = node.func
        if isinstance(f, ast.Name):
            self.calls.add(f.id)
        elif isinstance(f, ast.Attribute):
            self.calls.add(f.attr)
        self.generic_visit(node)


def func_info(node):
    rw = ReadWrite()
    rw.visit(node)
    info = FuncInfo(node)
    info.reads, info.writes, info.calls = rw.reads, rw.writes, rw.calls
    return info


def server_indicators(tree, node):
    """Return reasons the function must live on the server (may be empty)."""
    reasons = []
    src_imports = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            src_imports.update(a.asname or a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            src_imports.add(n.module.split(".")[0])
    info = func_info(node)
    hit = SERVER_MODULES & src_imports
    if hit and (info.reads & {i for i in info.reads} or info.calls):
        # Conservative: any server-only import in module + DB-ish call names.
        db_calls = {"query", "execute", "connect", "current", "create", "save", "where", "all"} & info.calls
        if db_calls or hit & {"psycopg", "psycopg2", "sqlalchemy", "sqlite3"}:
            reasons.append(f"uses server-only dependency {sorted(hit)[0]} / data access {sorted(db_calls)}")
    for name in info.reads | info.writes:
        if any(h in name.upper() for h in SECRET_HINTS):
            reasons.append(f"references secret-like symbol {name}")
            break
    decos = [ast.unparse(d) if hasattr(ast, "unparse") else "" for d in node.decorator_list]
    if any("server" in d or "task" in d for d in decos):
        reasons.append("explicit @server/@task marker")
    return reasons
