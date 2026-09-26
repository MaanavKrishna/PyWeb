"""Database layer for PyWeb (Track B).

Parameterized query builder, real SQLite driver (stdlib sqlite3) with a
connection-pool wrapper and transactions, schema/migration autogen, and a
Postgres adapter that requires psycopg when used.
"""

from __future__ import annotations

import os
import queue
import re
import sqlite3
import threading
from contextlib import contextmanager

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ident(name: str, kind: str = "identifier") -> str:
    if not isinstance(name, str) or not _IDENT.match(name):
        raise ValueError(f"invalid {kind}: {name!r}")
    return name


class Query:
    """Build PARAMETERIZED SQL only. Values always become placeholders."""

    def __init__(self, table: str, paramstyle: str = "qmark"):
        self._table = _ident(table, "table")
        if paramstyle not in ("qmark", "format"):
            raise ValueError(f"unknown paramstyle: {paramstyle!r}")
        self._paramstyle = paramstyle
        self._select: tuple = ("*",)
        self._wheres: list = []
        self._order: list = []
        self._limit: int | None = None

    @property
    def _ph(self) -> str:
        return "?" if self._paramstyle == "qmark" else "%s"

    def select(self, *cols: str):
        if cols:
            self._select = tuple(_ident(c, "column") for c in cols)
        return self

    def where(self, **conds):
        for col, val in conds.items():
            _ident(col, "column")
            self._wheres.append((col, val))
        return self

    def order_by(self, *cols: str):
        for col in cols:
            self._order.append(_ident(col.lstrip("-"), "column") if col.startswith("-") else _ident(col, "column"))
        self._raw_order = cols
        return self

    def limit(self, n: int):
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError(f"invalid LIMIT: {n!r}")
        self._limit = n
        return self

    def _where_clause(self, params: list) -> str:
        parts = []
        for col, val in self._wheres:
            if isinstance(val, (list, tuple)):
                if not val:
                    parts.append("0 = 1")
                else:
                    parts.append(f"{col} IN ({', '.join([self._ph] * len(val))})")
                    params.extend(val)
            elif val is None:
                parts.append(f"{col} IS NULL")
            else:
                parts.append(f"{col} = {self._ph}")
                params.append(val)
        return " AND ".join(parts)

    def build_select(self):
        params: list = []
        sql = f"SELECT {', '.join(self._select)} FROM {self._table}"
        if self._wheres:
            sql += " WHERE " + self._where_clause(params)
        if self._order:
            sql += " ORDER BY " + ", ".join(self._order)
        if self._limit is not None:
            sql += f" LIMIT {self._limit}"
        return sql, params

    def build_insert(self, data: dict):
        if not data:
            raise ValueError("cannot build INSERT with no columns")
        cols = [_ident(k, "column") for k in data]
        sql = (
            f"INSERT INTO {self._table} ({', '.join(cols)}) "
            f"VALUES ({', '.join([self._ph] * len(cols))})"
        )
        return sql, list(data.values())

    def build_update(self, data: dict):
        if not data:
            raise ValueError("cannot build UPDATE with no columns")
        if not self._wheres:
            raise ValueError("refusing unqualified UPDATE without WHERE")
        cols = [_ident(k, "column") for k in data]
        params: list = list(data.values())
        sql = f"UPDATE {self._table} SET " + ", ".join(f"{c} = {self._ph}" for c in cols)
        sql += " WHERE " + self._where_clause(params)
        return sql, params

    def build_delete(self):
        if not self._wheres:
            raise ValueError("refusing unqualified DELETE without WHERE")
        params: list = []
        return f"DELETE FROM {self._table} WHERE " + self._where_clause(params), params


class Result:
    def __init__(self, rows, columns, lastrowid, rowcount):
        self._rows = list(rows)
        self.columns = list(columns)
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def dicts(self):
        return [dict(zip(self.columns, r)) for r in self._rows]


class SQLiteDB:
    """SQLite driver over stdlib sqlite3 with a pooled wrapper + transactions."""

    paramstyle = "qmark"

    def __init__(self, path: str = ":memory:", pool_size: int = 5, timeout: float = 10.0):
        if path == ":memory:":
            pool_size = 1
        self.path = path
        self._pool: queue.Queue = queue.Queue()
        for _ in range(max(1, pool_size)):
            conn = sqlite3.connect(path, check_same_thread=False, timeout=timeout)
            self._pool.put(conn)
        self._local = threading.local()

    def _current(self):
        return getattr(self._local, "conn", None)

    def execute(self, sql: str, params=()):
        params = tuple(params)
        conn = self._current()
        owned = conn is None
        if owned:
            conn = self._pool.get()
        try:
            cur = conn.execute(sql, params)
            rows = cur.fetchall() if cur.description is not None else []
            cols = [d[0] for d in cur.description] if cur.description else []
            res = Result(rows, cols, cur.lastrowid, cur.rowcount)
            if owned:
                conn.commit()
            return res
        except Exception:
            if owned:
                conn.rollback()
            raise
        finally:
            if owned:
                self._pool.put(conn)

    @contextmanager
    def transaction(self):
        if self._current() is not None:
            yield self
            return
        conn = self._pool.get()
        self._local.conn = conn
        try:
            conn.execute("BEGIN")
            yield self
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._local.conn = None
            self._pool.put(conn)

    def close(self):
        while True:
            try:
                conn = self._pool.get_nowait()
            except queue.Empty:
                break
            try:
                conn.close()
            except Exception:
                pass


def snapshot(models) -> dict:
    return {m.__table__: m.schema_sql() for m in models}


def autogen(models, outdir: str = "migrations") -> dict:
    """Diff models into SQL up/down migration files stored in outdir."""
    os.makedirs(outdir, exist_ok=True)
    ups = ["-- PyWeb autogenerated migration (up)."]
    downs = ["-- PyWeb autogenerated migration (down)."]
    for m in models:
        ups.append(m.schema_sql() + ";")
    for m in reversed(list(models)):
        downs.append(f"DROP TABLE IF EXISTS {m.__table__};")
    up_path = os.path.join(outdir, "schema.up.sql")
    down_path = os.path.join(outdir, "schema.down.sql")
    with open(up_path, "w") as f:
        f.write("\n".join(ups) + "\n")
    with open(down_path, "w") as f:
        f.write("\n".join(downs) + "\n")
    return {"up": up_path, "down": down_path}


def _split_statements(script: str) -> list:
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def apply(db, outdir: str = "migrations", direction: str = "up") -> int:
    if direction not in ("up", "down"):
        raise ValueError(f"unknown direction: {direction!r}")
    path = os.path.join(outdir, f"schema.{direction}.sql")
    if not os.path.exists(path):
        raise FileNotFoundError(f"no such migration file: {path}")
    with open(path) as f:
        statements = _split_statements(f.read())
    with db.transaction():
        for stmt in statements:
            db.execute(stmt)
    return len(statements)


class PostgresDB:
    """Postgres adapter. Requires psycopg; raises a clear error otherwise."""

    paramstyle = "format"

    def __init__(self, dsn: str | None = None, **kwargs):
        try:
            import psycopg
        except ImportError as e:
            raise RuntimeError(
                "PostgresDB requires the 'psycopg' package, which is not "
                "installed. Install it with: pip install \"psycopg[binary]\""
            ) from e
        self._conn = psycopg.connect(dsn, **kwargs)
        self._conn.autocommit = False

    def execute(self, sql: str, params=()):
        with self._conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall() if cur.description is not None else []
            cols = [d[0] for d in cur.description] if cur.description else []
            return Result(rows, cols, None, cur.rowcount)

    @contextmanager
    def transaction(self):
        try:
            yield self
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass
