"""Durable jobs: the same contract for every store, plus workers, schedules, the mail
outbox, cleanup, the admin and the CLI."""

import os
import random
import threading
import time
import uuid

import pytest

from pyweb import jobs
from pyweb import models as M
from pyweb.db import connect, request_scope
from pyweb.jobs import core
from pyweb.jobs.memory import MemoryBackend


def make_store(kind, tmp_path):
    if kind == "memory":
        return MemoryBackend(), None
    if kind == "sqlite":
        db = connect(f"sqlite:///{tmp_path / 'jobs.db'}")
    else:
        url = os.environ.get(f"PYWEB_TEST_{kind.upper()}")
        if not url:
            pytest.skip(f"set PYWEB_TEST_{kind.upper()}")
        if kind == "redis":
            import redis

            from pyweb.jobs.redis import RedisBackend
            return RedisBackend(client=redis.Redis.from_url(url), prefix=f"pyweb:test:{uuid.uuid4().hex[:8]}:"), None
        db = connect(url)
        for t in ("pyweb_jobs", "pyweb_schedules"):
            try:
                db.execute(f"DROP TABLE {db.dialect.quote(t)}")
            except Exception:  # noqa: BLE001
                pass
    from pyweb.jobs.db import DatabaseBackend
    return DatabaseBackend(db), db


KINDS = ["memory", "sqlite", "postgres", "mysql", "redis"]


@pytest.fixture(params=KINDS)
def store(request, tmp_path, monkeypatch):
    jobs.stop_all(0)                      # e.g. a `pyweb dev` another test ran in this process
    monkeypatch.setattr(M._state, "db", None)
    b, db = make_store(request.param, tmp_path)
    if db is not None:
        M.use_database(db)
    jobs.use_backend(b)
    names = set(core.REGISTRY)
    yield b
    jobs.use_backend(None)
    for name in set(core.REGISTRY) - names:
        core.REGISTRY.pop(name, None)
        core.SCHEDULES.pop(name, None)
    M._state.db = None


def define(fn=None, **opts):
    """A job with a unique name for this test (the registry is global)."""
    def deco(f):
        return jobs.job(name=f"{f.__name__}_{uuid.uuid4().hex[:6]}", backoff=0.001, max_backoff=0.002, **opts)(f)
    return deco(fn) if fn else deco


def settle(store, handle, states=("done", "dead"), timeout=8):
    w = jobs.Worker(store, schedule=False, concurrency=4)
    end = time.time() + timeout
    while time.time() < end:
        w.tick()
        rec = store.get(handle.id)
        if rec and rec["state"] in states:
            return rec
        time.sleep(0.01)
    raise AssertionError(f"job still {store.get(handle.id)}")


# ------------------------------------------------------------------ contract

def test_runs_and_keeps_the_result(store):
    @define
    def add(a, b):
        jobs.progress(0.5)
        return {"sum": a + b, "attempt": jobs.current().attempt}

    h = add.enqueue(2, b=3)
    assert store.get(h.id)["state"] == "queued"
    rec = settle(store, h)
    assert rec["state"] == "done" and rec["result"] == {"sum": 5, "attempt": 1} and rec["progress"] == 1.0
    assert rec["args"] == {"args": [2], "kwargs": {"b": 3}}
    assert jobs.current() is None
    with pytest.raises(AttributeError):
        add(1, 1)                                         # calling it directly just runs it (no job context)


def test_arguments_must_be_json(store):
    @define
    def f(x):
        return x

    with pytest.raises(jobs.JobError, match="JSON"):
        f.enqueue(object())


def test_retries_then_succeeds(store):
    calls = []

    @define(retries=3)
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("try again")
        return "ok"

    rec = settle(store, flaky.enqueue())
    assert rec["state"] == "done" and rec["attempts"] == 3 and len(calls) == 3
    assert "ConnectionError" in (rec["last_error"] or "")


def test_dead_after_the_last_attempt_and_retry(store):
    state = {"fail": True}

    @define(retries=1)
    def broken():
        if state["fail"]:
            raise ValueError("bad input")
        return "fixed"

    h = broken.enqueue()
    rec = settle(store, h)
    assert rec["state"] == "dead" and rec["attempts"] == 2 and rec["last_error"].startswith("ValueError: bad input")
    state["fail"] = False
    assert jobs.retry(h.id) is True and jobs.retry(h.id) is False
    assert settle(store, h)["state"] == "done"


def test_delayed_jobs_wait(store):
    @define
    def later():
        return 1

    h = later.enqueue(delay=60)
    w = jobs.Worker(store, schedule=False)
    assert w.tick() == 0 and store.get(h.id)["state"] == "queued"
    assert w.tick(now=time.time() + 61) == 1
    assert settle(store, h)["state"] == "done"


def test_keys_dedupe_while_queued_and_running(store):
    @define
    def once(x):
        return x

    a = once.enqueue(1, key="k1")
    b = once.enqueue(2, key="k1")
    assert a.id == b.id
    settle(store, a)
    c = once.enqueue(3, key="k1")                       # finished, no window: a new job
    assert c.id != a.id


def test_unique_for_keeps_a_window_after_finishing(store):
    @define(unique_for=3600)
    def report(day):
        return day

    a = report.enqueue("2026-01-01")
    assert report.enqueue("2026-01-01").id == a.id
    assert report.enqueue("2026-01-02").id != a.id      # different arguments
    settle(store, a)
    assert report.enqueue("2026-01-01").id == a.id      # still inside the hour


def test_rollback_means_no_job(store):
    if M.database() is None:
        M.use_database(connect("sqlite:///:memory:"))   # the request's transaction needs a database
        M.database().execute("create table t (x int)")

    @define
    def side():
        return 1

    db = M.database()
    if not any(t == "t" for t in __import__("pyweb.db.schema", fromlist=["x"]).table_names(db)):
        db.execute("create table t (x int)")
    with pytest.raises(RuntimeError):
        with request_scope():
            db.execute("insert into t values (1)")
            h = side.enqueue()
            raise RuntimeError("the request failed")
    assert store.get(h.id) is None
    with request_scope():
        db.execute("insert into t values (2)")
        ok = side.enqueue()
    assert store.get(ok.id)["state"] == "queued"


def test_timeouts_count_as_failed_attempts(store):
    seen = []

    @define(retries=0, timeout=0.2)
    def slow():
        ctx = jobs.current()
        ctx.cancelled.wait(5)
        seen.append(ctx.cancelled.is_set())
        return "too late"

    h = slow.enqueue()
    rec = settle(store, h)
    assert rec["state"] == "dead" and "took longer than 0.2s" in rec["last_error"]
    time.sleep(0.1)
    assert seen == [True]                              # the job was told to stop
    assert store.get(h.id)["state"] == "dead"          # and its late result was ignored


def test_unknown_jobs_go_dead(store):
    store.enqueue(name="no_such_job", queue="default", payload="{}", run_at=time.time(), max_attempts=3,
                  timeout=30)
    w = jobs.Worker(store, schedule=False)
    w.tick()
    time.sleep(0.2)
    [rec] = store.list(name="no_such_job")
    assert rec["state"] == "dead" and "no job named" in rec["last_error"]


def test_an_expired_lease_lets_another_worker_finish(store):
    @define(retries=2, timeout=10)
    def work():
        return "finished"

    h = work.enqueue()
    now = time.time()
    [first] = store.claim(["default"], "dead-worker", 5, now=now)        # this worker then "dies"
    assert first["id"] == h.id and first["attempt"] == 1
    assert store.claim(["default"], "other", 5, now=now + 5) == []       # still leased
    [again] = store.claim(["default"], "other", 5, now=now + 41)         # lease = timeout + 30 s
    assert again["id"] == h.id and again["attempt"] == 2
    assert store.complete(first["id"], first["token"], "stale") is False  # the old claim lost it
    assert store.complete(again["id"], again["token"], "finished") is True
    assert store.get(h.id)["result"] == "finished"


def test_stopping_hands_running_jobs_back(store):
    release = threading.Event()

    @define
    def long_job():
        release.wait(5)
        return 1

    h = long_job.enqueue()
    w = jobs.Worker(store, schedule=False)
    w.tick()
    time.sleep(0.1)
    assert store.get(h.id)["state"] == "running"
    assert w.stop(timeout=0.1) == 1
    rec = store.get(h.id)
    assert rec["state"] == "queued" and rec["attempts"] == 0          # not counted as a failure
    release.set()
    assert settle(store, h)["state"] == "done"


def test_many_workers_run_each_job_once(store):
    counts = {}
    lock = threading.Lock()

    @define
    def count(i):
        with lock:
            counts[i] = counts.get(i, 0) + 1

    for i in range(40):
        count.enqueue(i)
    workers = [jobs.Worker(store, schedule=False, concurrency=3, name=f"w{n}") for n in range(4)]
    end = time.time() + 15
    while time.time() < end and sum(counts.values()) < 40:
        ts = [threading.Thread(target=w.tick) for w in workers]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        time.sleep(0.01)
    time.sleep(0.2)
    assert counts == {i: 1 for i in range(40)}


def test_purge_and_list(store):
    @define
    def quick():
        return 1

    hs = [quick.enqueue() for _ in range(3)]
    for h in hs:
        settle(store, h)
    assert len(store.list(state="done", name=quick.name)) == 3
    assert store.purge(time.time() - 3600) == 0
    assert store.purge(time.time() + 1) == 3
    assert store.list(name=quick.name) == []


def test_schedule_marks_only_move_forward(store):
    assert store.mark("s") is None
    store.set_mark("s", 100.0)
    store.set_mark("s", 50.0)
    store.set_mark("s", 150.0)
    assert store.mark("s") == 150.0


# --------------------------------------------------------------- unit bits

def test_backoff_grows_with_full_jitter():
    rng = random.Random(7)
    for attempt in range(1, 15):
        values = [core.backoff_delay(attempt, 2.0, 600.0, rng) for _ in range(200)]
        cap = min(600.0, 2.0 * 2 ** (attempt - 1))
        assert all(0 <= v <= cap for v in values)
        assert max(values) > cap * 0.8                 # spread over the whole range
    assert core.backoff_delay(30, 2.0, 600.0, rng) <= 600


def test_backend_follows_the_app_database(tmp_path, monkeypatch):
    monkeypatch.setattr(M._state, "db", None)
    jobs.use_backend(None)
    monkeypatch.delenv("PYWEB_JOBS", raising=False)
    assert type(jobs.backend()).__name__ == "MemoryBackend" and not jobs.durable()
    M.use_database(connect(f"sqlite:///{tmp_path / 'a.db'}"))
    first = jobs.backend()
    assert type(first).__name__ == "DatabaseBackend" and jobs.durable()
    M._state.db = None
    M.use_database(connect(f"sqlite:///{tmp_path / 'b.db'}"))
    assert jobs.backend() is not first
    monkeypatch.setenv("PYWEB_JOBS", "nonsense")
    jobs.use_backend(None)
    M._state.db = None
    with pytest.raises(jobs.JobError):
        jobs.backend()
    monkeypatch.delenv("PYWEB_JOBS")
    jobs.use_backend(None)


def test_db_jobs_commit_their_writes_with_done(tmp_path, monkeypatch):
    """With jobs in the app's database, a job whose lease was taken over can't also commit its writes."""
    monkeypatch.setattr(M._state, "db", None)
    db = connect(f"sqlite:///{tmp_path / 'once.db'}")
    M.use_database(db)
    from pyweb.jobs.db import DatabaseBackend
    store = jobs.use_backend(DatabaseBackend(db))
    db.execute("create table effects (note text)")
    try:
        @define
        def write(note, steal=False):
            db.execute("insert into effects values (?)", (note,))
            if steal:      # another worker reclaimed this job meanwhile
                db.execute("update pyweb_jobs set locked_by = 'someone-else' where id = ?", (jobs.current().id,))
            return note

        ok = write.enqueue("kept")
        lost = write.enqueue("dropped", steal=True)
        w = jobs.Worker(store, schedule=False)
        w.tick()
        time.sleep(0.3)
        assert store.get(ok.id)["state"] == "done"
        assert store.get(lost.id)["state"] == "running"      # left for whoever owns it now
        assert [r[0] for r in db.execute("select note from effects").fetchall()] == ["kept"]
    finally:
        jobs.use_backend(None)
        M._state.db = None


def test_a_failing_store_is_retried_with_backoff():
    calls = []

    class Broken(MemoryBackend):
        def claim(self, *a, **k):
            calls.append(time.monotonic())
            raise ConnectionError("database down")

    w = jobs.Worker(Broken(), schedule=False, poll=0.05).start()
    time.sleep(0.8)
    w.stop(0)
    assert 2 <= len(calls) <= 6                           # 0.05, 0.1, 0.2, 0.4 ... not every 50 ms
    gaps = [b - a for a, b in zip(calls, calls[1:])]
    assert gaps == sorted(gaps)
