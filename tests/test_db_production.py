"""Production DB: Postgres/MySQL over fake DB-API, pool, prepare, stream."""

from pyweb import db as _db


class FakeCursor:
    def __init__(self, conn, rows=(), cols=(), fail_first=None):
        self._conn = conn
        self._rows = list(rows)
        self._cols = list(cols)
        self.description = [(c,) for c in cols] or None
        self.lastrowid = 7
        self.rowcount = len(rows)
        self._fail_first = fail_first
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        if self._fail_first is not None:
            exc, self._fail_first = self._fail_first, None
            raise exc
        return self

    def fetchall(self):
        return self._rows

    def fetchmany(self, n):
        out, self._rows = self._rows[:n], self._rows[n:]
        return out

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows=(), cols=("id",), fail_first=None):
        self._rows, self._cols = rows, cols
        self._fail_first = fail_first
        self.cursors = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        c = FakeCursor(self, self._rows, self._cols, self._fail_first)
        self._fail_first = None
        self.cursors.append(c)
        return c

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def _pg(rows=((1,),), cols=("id",), **kw):
    conns = [FakeConn(rows, cols, **kw) for _ in range(kw.pop("n", 1))]
    it = iter(conns)
    d = _db.PostgresDB(connect=lambda: next(it, conns[0]))
    d._made = conns
    return d


def test_postgres_uses_format_placeholders_and_commits():
    d = _pg(rows=[(1,)], cols=["id"])
    res = d.execute("SELECT %s", (1,))
    assert res.fetchall() == [(1,)]
    assert d._made[0].commits == 1


def test_postgres_retries_deadlock_then_succeeds():
    class Deadlock(Exception):
        sqlstate = "40P01"
    d = _pg(rows=[(2,)], fail_first=Deadlock("deadlock detected"))
    res = d.execute("SELECT 1", attempts=2)
    assert res.fetchall() == [(2,)]
    assert d._made[0].rollbacks >= 1


def test_postgres_gives_up_and_raises_transient():
    class Deadlock(Exception):
        sqlstate = "40P01"

    class AlwaysDead(FakeConn):
        def cursor(self):
            return FakeCursor(self, [], ["id"], Deadlock("deadlock"))
    d = _db.PostgresDB(connect=lambda: AlwaysDead())
    try:
        d.execute("SELECT 1", attempts=2)
        raise AssertionError("should raise")
    except _db.TransientDBError:
        pass


def test_non_transient_error_not_retried():
    d = _pg(fail_first=ValueError("syntax error"))
    try:
        d.execute("SELECT 1", attempts=3)
        raise AssertionError("should raise")
    except ValueError:
        pass
    assert len(d._made[0].cursors) == 1  # exactly one attempt


def test_mysql_deadlock_errno_retries():
    class LockErr(Exception):
        errno = 1213
    conns = [FakeConn([(3,)], ["id"], fail_first=LockErr("deadlock"))]
    d = _db.MySQLDB(connect=lambda: conns[0])
    assert d.execute("SELECT 1", attempts=2).fetchall() == [(3,)]


def test_prepare_caches_statement():
    d = _pg()
    s1 = d.prepare("SELECT %s")
    s2 = d.prepare("SELECT %s")
    assert s1 == s2 == "SELECT %s"
    res = d.execute(s1, (1,), prepare=True)
    assert res.fetchall() == [(1,)]


def test_stream_pages_rows():
    d = _pg(rows=[(i,) for i in range(5)], cols=["n"])
    pages = list(d.stream("SELECT n", chunksize=2))
    assert [len(p.fetchall()) for p in pages] == [2, 2, 1]


def test_transaction_rolls_back_on_error():
    d = _pg()
    try:
        with d.transaction():
            d.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert d._made[0].rollbacks >= 1


def test_connect_url_routing():
    assert isinstance(_db.connect(":memory:"), _db.SQLiteDB)
    assert isinstance(_db.connect("sqlite:////tmp/x.db"), _db.SQLiteDB)


def test_sqlite_url_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _db.connect("sqlite:///rel.db").path == "rel.db"
    assert _db.connect(f"sqlite:///{tmp_path}/abs.db").path == f"{tmp_path}/abs.db"
    assert _db.connect(str(tmp_path / "plain.db")).path == str(tmp_path / "plain.db")
    try:
        _db.connect("bogus://x")
        raise AssertionError("should raise")
    except ValueError:
        pass


def test_query_builder_paramstyles():
    q = _db.Query("users", paramstyle="format").where(name="a").limit(1)
    sql, params = q.sql()
    assert "%s" in sql and list(params)[:1] == ["a"]
    q2 = _db.Query("users").where(name="a")
    assert "?" in q2.sql()[0]


def test_order_by_validates_and_supports_desc_prefix():
    import pytest
    from pyweb.db import Query
    sql, _ = Query("posts").order_by("-id").build_select()
    assert sql.endswith('ORDER BY "id" DESC')
    with pytest.raises(ValueError):
        Query("posts").order_by('id" ; drop table posts; --')


def test_a_handled_error_inside_a_transaction_keeps_earlier_writes(tmp_path):
    import sqlite3

    import pytest
    d = _db.connect(f"sqlite:///{tmp_path / 't.db'}")
    d.execute("create table t (id integer primary key, name text unique)")
    with d.transaction():
        d.execute("insert into t (name) values ('A')")
        with pytest.raises(sqlite3.IntegrityError):
            d.execute("insert into t (name) values ('A')")
        d.execute("insert into t (name) values ('B')")
    assert [r[0] for r in d.execute("select name from t order by id").fetchall()] == ["A", "B"]
    with pytest.raises(sqlite3.IntegrityError):         # an error that escapes rolls everything back
        with d.transaction():
            d.execute("insert into t (name) values ('C')")
            d.execute("insert into t (name) values ('A')")
    assert [r[0] for r in d.execute("select name from t order by id").fetchall()] == ["A", "B"]


def test_stream_inside_a_transaction_uses_its_connection():
    d = _db.connect(":memory:")                          # a pool of one connection
    d.execute("create table t (x)")
    d.execute("insert into t values (1)")
    with d.transaction():
        d.execute("insert into t values (2)")
        rows = [r for page in d.stream("select x from t order by x", chunksize=1) for r in page.fetchall()]
    assert [r[0] for r in rows] == [1, 2]                # no wait for a second connection; sees its own write
