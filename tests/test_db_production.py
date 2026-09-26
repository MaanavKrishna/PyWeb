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
    assert isinstance(_db.connect("sqlite:///tmp/x.db"), _db.SQLiteDB)
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
