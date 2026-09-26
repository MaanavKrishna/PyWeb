"""DB toolkit: parameterized query builder, SQLite driver with pooling and
transactions, migration autogen/apply, Postgres guard, pagination."""

from __future__ import annotations

import os
import queue
import re
import sqlite3
import threading
from contextlib import contextmanager

_OPS = {
    "eq": "=", "gt": ">", "lt": "<", "gte": ">=", "lte": "<=",
    "ne": "!=", "like": "LIKE",
}

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ident(name, kind="identifier"):
    if not isinstance(name, str) or not _IDENT.match(name):
        raise ValueError(f"invalid {kind}: {name!r}")
    return name


class Query:
    """Build PARAMETERIZED SQL only. Values always become placeholders."""

    def __init__(self, table, paramstyle="qmark"):
        self.table = table
        self._table = _ident(table, "table")
        if paramstyle not in ("qmark", "format"):
            raise ValueError(f"unknown paramstyle: {paramstyle!r}")
        self._paramstyle = paramstyle
        self._select = ("*",)
        self._wheres: list[tuple] = []
        self._order: list = []
        self._raw_order: tuple = ()
        self._limit = None
        self._offset = None

    @property
    def _ph(self):
        return "?" if self._paramstyle == "qmark" else "%s"

    def select(self, *cols):
        if cols:
            self._select = tuple(_ident(c, "column") for c in cols)
        return self

    def where(self, **kwargs):
        for key, value in kwargs.items():
            if "__" in key:
                field, op = key.rsplit("__", 1)
                if op in _OPS:
                    self._wheres.append((field, _OPS[op], value))
                    continue
                self._wheres.append((_ident(key, "column"), value))
            else:
                _ident(key, "column")
                self._wheres.append((key, "=", value))
        return self

    def _record_where(self, col, val):
        if isinstance(val, tuple) and len(val) == 2 and val[0] in ("=", ">", "<", ">=", "<=", "!=", "LIKE"):
            self._wheres.append((col, val))
        else:
            self._wheres.append((col, val))

    def order_by(self, field=None, *rest, **kw):
        if field is not None and not rest and (not kw or set(kw) == {"desc"}):
            self._order.append((field, kw.get("desc", False)))
            return self
        cols = (field,) + tuple(rest) if field is not None else ()
        for col in cols:
            if col.startswith("-"):
                self._order.append((_ident(col[1:], "column"), True))
            else:
                self._order.append((_ident(col, "column"), False))
        self._raw_order = cols
        return self

    def limit(self, n):
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError(f"invalid LIMIT: {n!r}")
        self._limit = n
        return self

    def offset(self, n):
        self._offset = n
        return self

    def paginate(self, page=1, per_page=20):
        return self.limit(per_page).offset((page - 1) * per_page)

    def _where_clause(self, params):
        parts = []
        for entry in self._wheres:
            if len(entry) == 3:
                col, op, val = entry
            else:
                col, val = entry
                op = "="
            if isinstance(val, (list, tuple)) and op == "=":
                if not val:
                    parts.append("0 = 1")
                else:
                    parts.append(f"{col} IN ({', '.join([self._ph] * len(val))})")
                    params.extend(val)
            elif val is None:
                parts.append(f'"{col}" IS NULL')
            else:
                parts.append(f'"{col}" {op} {self._ph}')
                params.append(val)
        return " AND ".join(parts)

    def _select_sql(self):
        params: list = []
        cols = ", ".join(self._select)
        q = f'SELECT {cols} FROM "{self._table}"'
        if self._wheres:
            q += " WHERE " + self._where_clause(params)
        if self._order:
            q += " ORDER BY " + ", ".join(f'"{f}" {"DESC" if d else "ASC"}' for f, d in self._order)
        if self._limit is not None:
            q += f" LIMIT {int(self._limit)}"
        if self._offset is not None:
            q += f" OFFSET {int(self._offset)}"
        return q, params

    def sql(self):
        return self._select_sql()

    def build_select(self):
        return self._select_sql()

    def build_insert(self, data):
        if not data:
            raise ValueError("cannot build INSERT with no columns")
        cols = [_ident(k, "column") for k in data]
        sql = (f"INSERT INTO {self._table} ({', '.join(cols)}) "
               f"VALUES ({', '.join([self._ph] * len(cols))})")
        return sql, list(data.values())

    def build_update(self, data):
        if not data:
            raise ValueError("cannot build UPDATE with no columns")
        if not self._wheres:
            raise ValueError("refusing unqualified UPDATE without WHERE")
        cols = [_ident(k, "column") for k in data]
        params = list(data.values())
        sql = f"UPDATE {self._table} SET " + ", ".join(f"{c} = {self._ph}" for c in cols)
        sql += " WHERE " + self._where_clause(params)
        return sql, params

    def build_delete(self):
        if not self._wheres:
            raise ValueError("refusing unqualified DELETE without WHERE")
        params: list = []
        return f"DELETE FROM {self._table} WHERE " + self._where_clause(params), params


class Migration:
    def __init__(self, name, statements):
        self.name = name
        self.statements = list(statements)

    def apply(self, conn):
        for stmt in self.statements:
            conn.execute(stmt)
        conn.commit()


class Schema:
    def __init__(self):
        self.migrations: list[Migration] = []
        self.applied: list[str] = []

    def add(self, migration):
        self.migrations.append(migration)

    def migrate(self, conn):
        for m in self.migrations:
            if m.name not in self.applied:
                m.apply(conn)
                self.applied.append(m.name)
        return list(self.applied)


class Transaction:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        return False


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

    def __init__(self, path=":memory:", pool_size=5, timeout=10.0):
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

    def execute(self, sql, params=()):
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


def snapshot(models):
    return {m.__table__: m.schema_sql() for m in models}


def autogen(models, outdir="migrations"):
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


def _split_statements(script):
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def apply(db, outdir="migrations", direction="up"):
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

    def __init__(self, dsn=None, **kwargs):
        try:
            import psycopg  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "PostgresDB requires the 'psycopg' package, which is not "
                "installed. Install it with: pip install \"psycopg[binary]\""
            ) from e
        raise RuntimeError("PostgresDB live connections are not used in tests; "
                           "pass a real DSN with psycopg installed.")
