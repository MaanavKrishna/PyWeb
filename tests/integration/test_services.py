"""Integration tests against real services (no fakes).

Set any of these to enable the matching tests (CI runs all three as
service containers):

    PYWEB_TEST_POSTGRES=postgresql://user@127.0.0.1:5432/db
    PYWEB_TEST_MYSQL=mysql://root:pw@127.0.0.1:3306/db
    PYWEB_TEST_REDIS=redis://127.0.0.1:6379/0
"""

import os
import threading
import uuid

import pytest

from pyweb import db as _db
from pyweb.db import migrate as _migrate

SQL_URLS = {
    "postgres": os.environ.get("PYWEB_TEST_POSTGRES"),
    "mysql": os.environ.get("PYWEB_TEST_MYSQL"),
    "sqlite": "sqlite://",
}


@pytest.fixture(params=["sqlite", "postgres", "mysql"])
def database(request):
    url = SQL_URLS[request.param]
    if not url:
        pytest.skip(f"set PYWEB_TEST_{request.param.upper()} to run against {request.param}")
    conn = _db.connect(url)
    table = f"t_{uuid.uuid4().hex[:8]}"
    conn.execute(f"create table {table} (id integer primary key, name varchar(100), score integer)")
    yield conn, table
    try:
        conn.execute(f"drop table {table}")
    finally:
        conn.close()


def test_portable_placeholders_and_dicts(database):
    conn, t = database
    conn.execute(f"insert into {t} (id, name, score) values (?, ?, ?)", (1, "ada", 90))
    conn.execute(f"insert into {t} (id, name, score) values (?, ?, ?)", (2, "50% off 'deal'", 10))
    rows = conn.execute(f"select id, name, score from {t} where score > ? order by id", (5,)).dicts()
    assert rows == [{"id": 1, "name": "ada", "score": 90},
                    {"id": 2, "name": "50% off 'deal'", "score": 10}]
    like = conn.execute(f"select name from {t} where name like ?", ("%off%",)).fetchall()
    assert [r[0] for r in like] == ["50% off 'deal'"]


def test_transaction_rollback_really_rolls_back(database):
    conn, t = database
    with pytest.raises(RuntimeError):
        with conn.transaction():
            conn.execute(f"insert into {t} (id, name, score) values (?, ?, ?)", (1, "x", 1))
            raise RuntimeError("boom")
    assert conn.execute(f"select count(*) from {t}").fetchall()[0][0] == 0
    with conn.transaction():
        conn.execute(f"insert into {t} (id, name, score) values (?, ?, ?)", (1, "x", 1))
    assert conn.execute(f"select count(*) from {t}").fetchall()[0][0] == 1


def test_streaming_large_result(database):
    conn, t = database
    with conn.transaction():
        for i in range(250):
            conn.execute(f"insert into {t} (id, name, score) values (?, ?, ?)", (i, f"n{i}", i))
    pages = list(conn.stream(f"select id from {t} where score >= ? order by id", (0,), chunksize=100))
    assert [len(p.fetchall()) for p in pages] == [100, 100, 50]


def test_pool_is_thread_safe(database):
    conn, t = database
    errors = []

    def worker(n):
        try:
            for i in range(10):
                conn.execute(f"insert into {t} (id, name, score) values (?, ?, ?)", (n * 100 + i, "w", i))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    if SQL_URLS["sqlite"] and conn.__class__ is _db.SQLiteDB and conn.path in (":memory:", ""):
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(1)]
    else:
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors
    assert conn.execute(f"select count(*) from {t}").fetchall()[0][0] == 10 * len(threads)


def test_migrations_apply_and_roll_back(database, tmp_path):
    conn, _t = database
    d = str(tmp_path / "migrations")
    suffix = uuid.uuid4().hex[:6]
    path = _migrate.new_migration(d, "create widgets")
    with open(path, "a") as fh:
        fh.write(f"CREATE TABLE widgets_{suffix} (id INTEGER PRIMARY KEY);")
    with open(path.replace(".up.sql", ".down.sql"), "a") as fh:
        fh.write(f"DROP TABLE widgets_{suffix};")
    try:
        assert _migrate.migrate(conn, d) == ["001_create_widgets"]
        assert _migrate.migrate(conn, d) == []
        conn.execute(f"insert into widgets_{suffix} (id) values (?)", (1,))
        assert _migrate.rollback(conn, d) == ["001_create_widgets"]
        assert _migrate.status(conn, d) == [("001_create_widgets", False)]
    finally:
        try:
            conn.execute("drop table pyweb_migrations")
        except Exception:  # noqa: BLE001
            pass


REDIS = os.environ.get("PYWEB_TEST_REDIS")
needs_redis = pytest.mark.skipif(not REDIS, reason="set PYWEB_TEST_REDIS to run Redis tests")


@needs_redis
def test_redis_bus_shares_history_across_processes():
    from pyweb.realtime import RedisBus
    prefix = f"pyweb-test-{uuid.uuid4().hex[:6]}:"
    a, b = RedisBus(REDIS, prefix=prefix), RedisBus(REDIS, prefix=prefix)
    got = []
    a.channel("room").subscribe(got.append)
    a.publish("room", {"n": 1})
    b.publish("room", {"n": 2})
    a.publish("room", "three")
    assert got == [{"n": 1}, "three"]          # local subscribers of `a`
    assert b.since("room", 0) == [(1, {"n": 1}), (2, {"n": 2}), (3, "three")]
    assert a.since("room", 2) == [(3, "three")]
    assert a.since("room", 3) == []


@needs_redis
def test_redis_queue_runs_jobs_in_another_worker():
    from pyweb.jobs import RedisQueue
    prefix = f"pyweb-test-{uuid.uuid4().hex[:6]}:"
    producer, worker = RedisQueue(REDIS, prefix=prefix), RedisQueue(REDIS, prefix=prefix)

    def add(a, b):
        return a + b

    worker.register(add)
    job = producer.submit(add, 2, 3)
    assert worker.drain() is not None
    assert worker.drain() is None              # empty queue returns immediately
    assert producer.status(job.id) == {"fn": "add", "done": True, "failed": False,
                                       "result": 5, "error": None}


LIVE_APP = '''import os

from pyweb import App, live, server
from pyweb.db import connect
from pyweb.realtime import RedisBus, use_bus

use_bus(RedisBus(os.environ["PYWEB_TEST_REDIS"], prefix=os.environ["LIVE_PREFIX"]))
app = App()
db = connect("sqlite:///" + os.environ["LIVE_DB"])
db.execute("create table if not exists items (id integer primary key, name text)")


@server
def add(name: str) -> None:
    db.execute("insert into items (name) values (?)", (name,))


@app.page("/")
def Items():
    items = live(db, "select name from items order by id")

    <ul>
        for item in items:
            <li>{item["name"]}</li>
    </ul>
'''


@needs_redis
def test_live_queries_work_across_processes(tmp_path):
    """Page rendered by A, browser connected to B, write made by C: B still delivers the new rows."""
    import json
    import re
    import socket
    import subprocess
    import sys
    import time
    import urllib.parse
    import urllib.request

    (tmp_path / "app.pyweb").write_text(LIVE_APP)
    env = {**os.environ, "LIVE_DB": str(tmp_path / "live.db"), "LIVE_PREFIX": f"pyweb-test-{uuid.uuid4().hex[:6]}:",
           "PYWEB_AUTH_SECRET": "s" * 32}

    def free_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    ports = [free_port() for _ in range(3)]
    procs = [subprocess.Popen([sys.executable, "-m", "pyweb.cli", "dev", "app.pyweb", "--no-reload", "--port", str(p)],
                              cwd=tmp_path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             for p in ports]
    try:
        for p in ports:
            for _ in range(100):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{p}/healthz", timeout=1)
                    break
                except OSError:
                    time.sleep(0.1)
        a, b, c = (f"http://127.0.0.1:{p}" for p in ports)
        html = urllib.request.urlopen(a + "/").read().decode()
        state = json.loads(re.search(r'<script id="pw-state" type="application/json">(.*?)</script>', html).group(1))
        meta = state["$live:items"]
        query = urllib.parse.urlencode({"feed": meta["feed"], "live": meta["spec"]})
        stream = urllib.request.urlopen(f"{b}/__pyweb/events?{query}", timeout=10)
        stream.readline()                                    # "retry:" — B is now following the query
        time.sleep(0.3)
        req = urllib.request.Request(c + "/__pyweb/rpc/add", data=json.dumps({"args": {"name": "x"}}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        deadline = time.time() + 8
        rows = None
        while time.time() < deadline and rows is None:
            line = stream.readline().decode()
            if line.startswith("data:"):
                rows = json.loads(line[5:])["rows"]
        assert rows == [{"name": "x"}]
    finally:
        for proc in procs:
            proc.terminate()
            proc.wait(5)
