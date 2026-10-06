"""DB toolkit: parameterized query builder, SQLite driver with pooling and
transactions, migration autogen/apply, Postgres guard, pagination."""

from __future__ import annotations

import contextvars
import logging
import os
import queue
import re
import threading
import time
import weakref
from contextlib import contextmanager

from .dialect import MYSQL, POSTGRES, SQLITE, Dialect, dialect_of  # noqa: F401 - re-exported

log = logging.getLogger("pyweb.db")

#: Functions called after every statement: ``hook(db, sql, params, milliseconds, rowcount)``
#: (the dev toolbar and metrics use this).
QUERY_HOOKS: list = []

_scope: contextvars.ContextVar = contextvars.ContextVar("pyweb_db_scope", default=None)

_OPS = {
    "eq": "=", "gt": ">", "lt": "<", "gte": ">=", "lte": "<=",
    "ne": "!=", "like": "LIKE",
}

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def qmark_to_format(sql):
    """``?`` → ``%s`` outside quotes; literal ``%`` → ``%%``.

    SQL that already uses ``%s`` (no ``?`` placeholders) is returned
    unchanged, so driver-native SQL keeps working.
    """
    out, quote, found = [], None, False
    for ch in sql:
        if quote:
            out.append("%%" if ch == "%" else ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "?":
            found = True
            out.append("%s")
        elif ch == "%":
            out.append("%%")
        else:
            out.append(ch)
    return "".join(out) if found else sql


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
            desc = kw.get("desc", False)
            if field.startswith("-"):
                field, desc = field[1:], True
            self._order.append((_ident(field, "column"), desc))
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


class TransientDBError(Exception):
    """Retriable error (deadlock, serialization failure, conn reset)."""


#: Every database opened with connect(), for readiness checks (/readyz).
OPEN: "weakref.WeakSet" = weakref.WeakSet()


class _PooledDB:
    """Shared DB-API pool: thread-local transactions, prepared statements,
    streaming cursors, prepared-statement cache, retriable-error mapping.

    Why one base: SQLite/Postgres/MySQL all speak DB-API; the differences
    are connect(), paramstyle, and error classes. Alternatives considered:
    SQLAlchemy (heavy dep, hides the SQL we want visible for placement
    analysis). This keeps ``Query``-built SQL inspectable by the compiler.
    """

    paramstyle = "qmark"
    dialect: Dialect = SQLITE
    replica = None

    def _connect(self):
        raise NotImplementedError

    def _is_transient(self, exc: Exception) -> bool:
        return False

    # -- transactions on a raw connection ----------------------------------
    @staticmethod
    def _raw(conn, sql):
        if hasattr(conn, "execute"):
            cur = conn.execute(sql)
        else:
            cur = conn.cursor()
            cur.execute(sql)
        try:
            cur.close()
        except Exception:
            pass

    def _begin(self, conn):
        """Start a transaction on ``conn``; returns what :meth:`_end` needs to restore."""
        prev_autocommit = None
        if isinstance(getattr(conn, "autocommit", None), bool):
            # Drivers in autocommit mode (Postgres/MySQL pools) must leave it for
            # the duration, or every statement commits and rollback is a no-op.
            prev_autocommit = conn.autocommit
            conn.autocommit = False
        elif hasattr(conn, "execute"):
            try:
                conn.execute("BEGIN")
            except Exception:
                pass
        self._local.conn = conn
        self._local.depth = 0
        return prev_autocommit

    def _end(self, conn, prev_autocommit, *, commit):
        try:
            if commit:
                conn.commit()
            else:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if prev_autocommit is not None:
                try:
                    conn.autocommit = prev_autocommit
                except Exception:
                    pass
            self._local.conn = None
            pending, self._local.pending = getattr(self._local, "pending", None), None
            callbacks, self._local.callbacks = getattr(self._local, "callbacks", None), None
            finals, self._local.finals = getattr(self._local, "finals", None), None
            self._pool.put(conn)
        if commit and pending:
            from pyweb import livedata
            livedata.table_changed(self, *pending)
        if commit and callbacks:
            for _depth, fn in callbacks:
                try:
                    fn()
                except Exception:  # noqa: BLE001 - the transaction already committed
                    log.exception("after_commit callback failed")
        for fn in finals or ():
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("after_end callback failed")

    def after_end(self, fn):
        """Call ``fn()`` when the current transaction ends, committed or rolled back (at once outside
        one). For records that must survive a failed request, such as an audit log."""
        if self._current() is None:
            fn()
            return
        finals = getattr(self._local, "finals", None)
        if finals is None:
            finals = self._local.finals = []
        finals.append(fn)

    def after_commit(self, fn):
        """Call ``fn()`` once the current transaction commits (at once outside a transaction).

        A rollback (of the transaction, or of the savepoint the call was made in) drops it.
        """
        if self._current() is None:
            fn()
            return
        callbacks = getattr(self._local, "callbacks", None)
        if callbacks is None:
            callbacks = self._local.callbacks = []
        callbacks.append((getattr(self._local, "depth", 0), fn))

    def _join_scope(self):
        """Inside a request scope, start its transaction on this database now (before the first write)."""
        if self._current() is None:
            scope = _scope.get()
            if scope is not None:
                scope.begin(self)

    def __init__(self, pool_size=5, timeout=10.0, statement_cache=128,
                 connect_kwargs=None):
        # Connections open lazily (up to pool_size), so importing an app
        # never fails just because the database is briefly unreachable.
        self._pool: queue.Queue = queue.Queue()
        self._pool_size = max(1, pool_size)
        self._pool_timeout = timeout
        self._created = 0
        self._create_lock = threading.Lock()
        self._local = threading.local()
        self._stmt_cache_size = statement_cache
        self._stmt_cache: dict[str, str] = {}
        OPEN.add(self)

    def ping(self):
        """Raise if the database can't answer a trivial query (readiness checks)."""
        self.execute("SELECT 1")

    def _current(self):
        return getattr(self._local, "conn", None)

    # live data ---------------------------------------------------------
    def live_identity(self):
        """What identifies this database across processes (no password)."""
        dsn = getattr(self, "_dsn", None)
        if not dsn:
            return f"{type(self).__name__}:{id(self)}"
        return re.sub(r"//([^:/@]+):[^@]*@", r"//\1@", str(dsn))

    def notify(self, *tables):
        """Tell live queries that ``tables`` changed (for writes PyWeb didn't make itself)."""
        from pyweb import livedata
        livedata.table_changed(self, *tables)

    def _wrote(self, sql):
        from pyweb import livedata
        if not livedata.enabled():
            return
        table = livedata.written_table(sql)
        if table is None:
            return
        if self._current() is not None:          # inside a transaction: announce after COMMIT
            pending = getattr(self._local, "pending", None)
            if pending is None:
                pending = self._local.pending = set()
            pending.add(table)
        else:
            livedata.table_changed(self, table)

    def _acquire(self):
        try:
            return self._pool.get_nowait()
        except queue.Empty:
            pass
        with self._create_lock:
            if self._created < self._pool_size:
                conn = self._connect()
                self._created += 1
                return conn
        try:
            return self._pool.get(timeout=self._pool_timeout)
        except queue.Empty:
            raise TransientDBError(
                f"no database connection available within {self._pool_timeout}s "
                f"(pool_size={self._pool_size})") from None

    def _adapt(self, sql, params):
        """Rewrite portable ``?`` placeholders for ``format``-style drivers."""
        if self.paramstyle != "format" or not params:
            return sql
        return qmark_to_format(sql)

    def prepare(self, sql: str) -> str:
        """Cache/validate a statement; returns the (possibly rewritten) SQL.

        DB-API has no cross-driver prepare handle, so this caches the
        validated statement text and returns it for ``execute(prepared)``.
        Drivers that support server-side prepares (psycopg) get them via
        ``execute(..., prepare=True)``.
        """
        cached = self._stmt_cache.get(sql)
        if cached is not None:
            return cached
        if len(self._stmt_cache) >= self._stmt_cache_size:
            self._stmt_cache.pop(next(iter(self._stmt_cache)))
        self._stmt_cache[sql] = sql
        return sql

    def execute(self, sql, params=(), *, prepare=False, attempts=1):
        params = tuple(params)
        conn = self._current()
        if conn is None:
            scope = _scope.get()
            if scope is not None and _is_write(sql):
                conn = scope.begin(self)
            elif self.replica is not None and _is_read(sql):
                return self.replica.execute(sql, params, prepare=prepare, attempts=attempts)
        if not QUERY_HOOKS and _SLOW_MS is None:
            return self._execute(conn, sql, params, prepare, attempts)
        start = time.perf_counter()
        res = self._execute(conn, sql, params, prepare, attempts)
        ms = (time.perf_counter() - start) * 1000
        for hook in list(QUERY_HOOKS):
            try:
                hook(self, sql, params, ms, res.rowcount)
            except Exception:  # noqa: BLE001 - a broken hook must not break queries
                pass
        if _SLOW_MS is not None and ms >= _SLOW_MS:
            self._log_slow(sql, params, ms)
        return res

    def _log_slow(self, sql, params, ms):
        plan = ""
        if _is_read(sql) and not getattr(self._local, "explaining", False):
            self._local.explaining = True
            try:
                rows = self._execute(self._current(), f"{self.dialect.explain} {sql}", params, False, 1).fetchall()
                plan = "\n  " + "\n  ".join(" ".join(str(x) for x in r) for r in rows)
            except Exception:  # noqa: BLE001
                plan = ""
            finally:
                self._local.explaining = False
        log.warning("slow query (%.0f ms): %s%s", ms, " ".join(sql.split()), plan)

    def _execute(self, conn, sql, params, prepare, attempts):
        owned = conn is None
        if owned:
            conn = self._acquire()
        sql = self._adapt(sql, params)
        try:
            last: Exception | None = None
            for _ in range(max(1, attempts)):
                try:
                    if prepare:
                        sql = self.prepare(sql)
                    if hasattr(conn, "execute"):
                        cur = conn.execute(sql, params)
                    else:  # psycopg-style cursor protocol
                        cur = conn.cursor()
                        cur.execute(sql, params)
                    rows = cur.fetchall() if cur.description is not None else []
                    cols = [d[0] for d in cur.description] if cur.description else []
                    lastrowid = getattr(cur, "lastrowid", None)
                    res = Result(rows, cols, lastrowid, cur.rowcount)
                    try:
                        cur.close()
                    except Exception:
                        pass
                    if owned:
                        conn.commit()
                    self._wrote(sql)
                    return res
                except Exception as exc:  # noqa: BLE001
                    last = exc
                    if not owned:
                        # Inside transaction(): rolling back here would drop the
                        # transaction's earlier writes while later ones still commit.
                        # transaction() rolls back if the error escapes it.
                        raise
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    if not self._is_transient(exc):
                        raise
            raise TransientDBError(
                f"transient failure after {attempts} attempts: {last}")
        finally:
            if owned:
                self._pool.put(conn)

    def stream(self, sql, params=(), *, chunksize=1000):
        """Yield ``Result`` pages without loading the full result set."""
        conn = self._current()            # inside transaction(): use (and see) its connection
        owned = conn is None
        if owned:
            conn = self._acquire()
        sql = self._adapt(sql, params)
        try:
            cur = conn.cursor() if hasattr(conn, "cursor") else None
            if cur is None:
                res = self.execute(sql, params)
                yield res
                return
            cur.execute(sql, tuple(params))
            cols = [d[0] for d in cur.description] if cur.description else []
            while True:
                rows = cur.fetchmany(chunksize)
                if not rows:
                    break
                yield Result(rows, cols, None, len(rows))
            try:
                cur.close()
            except Exception:
                pass
        finally:
            if owned:
                self._pool.put(conn)

    @contextmanager
    def transaction(self):
        """Run a block in a transaction: committed at the end, rolled back on error.

        Nested blocks become savepoints, so an inner block can fail (and be
        caught) without undoing the outer one.
        """
        conn = self._current()
        if conn is not None:
            depth = getattr(self._local, "depth", 0) + 1
            self._local.depth = depth
            name = f"pyweb_sp_{depth}"
            self._raw(conn, f"SAVEPOINT {name}")
            try:
                yield self
            except BaseException:
                self._raw(conn, f"ROLLBACK TO SAVEPOINT {name}")
                self._raw(conn, f"RELEASE SAVEPOINT {name}")
                callbacks = getattr(self._local, "callbacks", None)
                if callbacks:
                    self._local.callbacks = [(d, f) for d, f in callbacks if d < depth]
                raise
            else:
                self._raw(conn, f"RELEASE SAVEPOINT {name}")
            finally:
                self._local.depth = depth - 1
            return
        conn = self._acquire()
        try:
            prev = self._begin(conn)
        except BaseException:
            self._pool.put(conn)
            raise
        try:
            yield self
        except BaseException:
            self._local.pending = None
            self._local.callbacks = None
            self._end(conn, prev, commit=False)
            raise
        self._end(conn, prev, commit=True)

    @contextmanager
    def migration_transaction(self):
        """The transaction a migration runs in (SQLite turns off foreign key actions for it)."""
        with self.transaction():
            yield self

    def close(self):
        while True:
            try:
                conn = self._pool.get_nowait()
            except queue.Empty:
                break
            with self._create_lock:
                self._created -= 1
            try:
                conn.close()
            except Exception:
                pass

    def __del__(self):  # best-effort: never leak pool connections at GC
        try:
            self.close()
        except Exception:
            pass


class SQLiteDB(_PooledDB):
    """SQLite driver over stdlib sqlite3 with a pooled wrapper + transactions.

    Foreign keys are enforced, and file databases use write-ahead logging so
    readers never wait for a writer.
    """

    paramstyle = "qmark"
    dialect = SQLITE

    def __init__(self, path=":memory:", pool_size=5, timeout=10.0, **kw):
        if path == ":memory:":
            pool_size = 1
        self.path = path
        self._dsn = None if path == ":memory:" else "sqlite:" + os.path.abspath(path)
        self._timeout = timeout
        self._kw = kw
        super().__init__(pool_size=pool_size, timeout=timeout)

    def _connect(self):
        import sqlite3  # imported on use: some Pythons (e.g. Pyodide) ship it separately
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=self._timeout)
        conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:" and not str(self.path).startswith("file:"):
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.DatabaseError:
                pass
        return conn

    @contextmanager
    def migration_transaction(self):
        # SQLite can only change some things by rebuilding a table, and dropping the
        # old copy would fire ON DELETE CASCADE; so foreign keys are off while a
        # migration runs and checked before it commits.
        if self._current() is not None:
            with self.transaction():
                yield self
            return
        conn = self._acquire()
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            self._begin(conn)
            try:
                yield self
                problems = conn.execute("PRAGMA foreign_key_check").fetchall()
                if problems:
                    raise RuntimeError(f"migration left rows pointing at missing rows: {problems[:5]}")
            except BaseException:
                self._local.pending = None
                self._local.callbacks = None
                self._local.conn = None
                conn.rollback()
                raise
            conn.commit()
        except BaseException:
            conn.execute("PRAGMA foreign_keys = ON")
            if self._local.conn is not None:
                self._local.conn = None
            self._pool.put(conn)
            raise
        conn.execute("PRAGMA foreign_keys = ON")
        self._local.conn = None
        self._pool.put(conn)


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


class PostgresDB(_PooledDB):
    """PostgreSQL driver over psycopg (v3) or psycopg2.

    Retries deadlocks (40P01) and serialization failures (40001);
    autocommits single statements outside explicit transactions.
    """

    paramstyle = "format"
    dialect = POSTGRES

    TRANSIENT_CODES = frozenset({"40P01", "40001", "55P03", "08006", "08003"})

    def __init__(self, dsn=None, *, pool_size=5, timeout=10.0,
                 connect=None, **kwargs):
        self._dsn = dsn
        if connect is not None:
            self._factory = connect
            self._psycopg = None
        else:
            try:
                import psycopg as _pg
                self._factory = lambda: _pg.connect(dsn or "", **kwargs)
                self._psycopg = _pg
            except ImportError:
                try:
                    import psycopg2 as _pg2
                    self._factory = lambda: _pg2.connect(dsn or "", **kwargs)
                    self._psycopg = _pg2
                except ImportError as e:
                    raise RuntimeError(
                        "PostgresDB requires the 'psycopg' package: "
                        "pip install \"psycopg[binary]\"") from e
        super().__init__(pool_size=pool_size, timeout=timeout)

    def _connect(self):
        conn = self._factory()
        try:
            conn.autocommit = True
        except Exception:
            pass
        return conn

    def _is_transient(self, exc):
        code = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
        if code in self.TRANSIENT_CODES:
            return True
        msg = str(exc).lower()
        return any(k in msg for k in ("deadlock", "serialization failure",
                                      "connection reset", "server closed"))


class MySQLDB(_PooledDB):
    """MySQL driver over mysql-connector-python or PyMySQL.

    Retries deadlocks (1213) and lock timeouts (1205).
    """

    paramstyle = "format"
    dialect = MYSQL

    TRANSIENT_CODES = frozenset({1213, 1205, 2006, 2013})

    def __init__(self, dsn=None, *, pool_size=5, timeout=10.0,
                 connect=None, **kwargs):
        self._dsn = dsn
        if connect is not None:
            self._factory = connect
        else:
            self._factory = self._default_factory(dsn, kwargs)
        super().__init__(pool_size=pool_size, timeout=timeout)

    @staticmethod
    def _default_factory(dsn, kwargs):
        try:
            import mysql.connector as _mc

            def make():
                if dsn:
                    from urllib.parse import urlparse as _up
                    u = _up(dsn)
                    return _mc.connect(
                        host=u.hostname or "localhost",
                        port=u.port or 3306,
                        user=u.username or "",
                        password=u.password or "",
                        database=(u.path or "/")[1:] or None,
                        **kwargs)
                return _mc.connect(**kwargs)
            return make
        except ImportError:
            pass
        try:
            import pymysql as _pm

            def make2():
                if dsn:
                    from urllib.parse import urlparse as _up2
                    u = _up2(dsn)
                    return _pm.connect(
                        host=u.hostname or "localhost",
                        port=u.port or 3306,
                        user=u.username or "",
                        password=u.password or "",
                        database=(u.path or "/")[1:] or None,
                        **kwargs)
                return _pm.connect(**kwargs)
            return make2
        except ImportError as e:
            raise RuntimeError(
                "MySQLDB requires 'mysql-connector-python' or 'PyMySQL': "
                "pip install mysql-connector-python") from e

    def _connect(self):
        return self._factory()

    def _is_transient(self, exc):
        code = getattr(exc, "errno", None)
        if code in self.TRANSIENT_CODES:
            return True
        msg = str(exc).lower()
        return "deadlock" in msg or "lock wait timeout" in msg


def connect(url: str, *, replica=None, **kwargs):
    """Open a database from a URL: ``sqlite://``, ``postgres://``,
    ``postgresql://``, ``mysql://``. ``:memory:`` SQLite for tests.

    ``replica`` (a URL or database) receives reads made outside transactions;
    a request that has written reads its own writes from the primary.
    """
    db = _connect_url(url, **kwargs)
    if replica:
        db.replica = connect(replica) if isinstance(replica, str) else replica
    return db


def _connect_url(url, **kwargs):
    from urllib.parse import urlparse as _up
    if url in (":memory:", "sqlite:///:memory:", "sqlite://"):
        return SQLiteDB(":memory:")
    u = _up(url)
    scheme = u.scheme.lower()
    if scheme in ("sqlite", "sqlite3", ""):
        # SQLAlchemy convention: sqlite:///app.db is relative to the working
        # directory, sqlite:////var/data/app.db is absolute.
        if "://" in url:
            rest = url.split("://", 1)[1]
            path = rest[1:] if rest.startswith("/") else rest
        else:
            path = url  # a plain filesystem path
        return SQLiteDB(path or ":memory:", **kwargs)
    if scheme in ("postgres", "postgresql"):
        return PostgresDB(url, **kwargs)
    if scheme == "mysql":
        return MySQLDB(url, **kwargs)
    raise ValueError(f"unknown database scheme: {scheme!r} in {url!r}")


# ------------------------------------------------------------ request scope

_WRITE_START = re.compile(r"^\s*(insert|update|delete|replace|merge|upsert)\b", re.I)
_READ_START = re.compile(r"^\s*(select|with|values|show|pragma\s+table_info|explain)\b", re.I)


def _is_write(sql):
    from pyweb import livedata
    return bool(_WRITE_START.match(sql)) or livedata.written_table(sql) is not None or \
        bool(re.search(r"\bfor\s+update\b", sql, re.I))


def _is_read(sql):
    return bool(_READ_START.match(sql)) and not re.search(r"\bfor\s+update\b", sql, re.I) and \
        livedata_written(sql) is None


def livedata_written(sql):
    from pyweb import livedata
    return livedata.written_table(sql)


def _slow_threshold():
    raw = os.environ.get("PYWEB_SLOW_QUERY_MS")
    if raw is None:
        if os.environ.get("PYWEB_ENV", "").strip().lower() == "production":
            return None
        return 200.0
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


_SLOW_MS = _slow_threshold()


class _Scope:
    def __init__(self):
        self.open = []          # (db, conn, prev_autocommit, thread id)

    def begin(self, db):
        conn = db._acquire()
        try:
            prev = db._begin(conn)
        except BaseException:
            db._pool.put(conn)
            raise
        self.open.append((db, conn, prev))
        return conn

    def finish(self, commit):
        errors = []
        for db, conn, prev in self.open:
            try:
                db._end(conn, prev, commit=commit and not errors)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        self.open = []
        if errors:
            raise errors[0]


@contextmanager
def request_scope():
    """Make a request's writes one transaction: it starts at the first write and
    commits when the block ends (or rolls back on an error). Reads before the
    first write don't hold a connection. Nested scopes join the outer one.

    PyWeb runs every RPC call and form action inside one (opt out with
    ``@atomic(False)``).
    """
    if _scope.get() is not None:
        yield _scope.get()
        return
    scope = _Scope()
    token = _scope.set(scope)
    try:
        yield scope
    except BaseException:
        _scope.reset(token)
        scope.finish(commit=False)
        raise
    _scope.reset(token)
    scope.finish(commit=True)


def atomic(fn=None, *, enabled=True):
    """``@atomic`` runs a function in one transaction (the request scope);
    ``@atomic(False)`` opts an RPC function out of the automatic one."""
    import functools
    if fn is False or fn is True:
        enabled, fn = fn, None

    def deco(f):
        if not enabled:
            f.__pyweb_atomic__ = False
            return f

        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            with request_scope():
                return f(*args, **kwargs)
        wrapper.__pyweb_atomic__ = True
        return wrapper
    return deco(fn) if fn is not None else deco


def in_scope(fn, kwargs):
    """Call ``fn(**kwargs)`` inside a request scope unless it opted out."""
    if getattr(fn, "__pyweb_atomic__", None) is False:
        return fn(**kwargs)
    with request_scope():
        return fn(**kwargs)
