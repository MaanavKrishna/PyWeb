"""Jobs queue, cache + SWR, realtime bus/channels."""


from pyweb.cache import MemoryCache, cache, swr
from pyweb.jobs import Queue, task
from pyweb.realtime import Bus, Channel, realtime


def test_job_success_and_progress():
    q = Queue()

    def work(x, _job=None):
        _job.set_progress(0.5)
        return x * 2
    job = q.submit(work, 21)
    q.wait(job)
    assert job.result == 42 and not job.pending and job.progress == 0.5


def test_job_without_job_kwarg():
    q = Queue()
    job = q.submit(lambda: "ok")
    q.wait(job)
    assert job.result == "ok"


def test_job_retry_then_success():
    q = Queue()
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("boom")
        return "fine"
    job = q.submit(flaky, retries=2)
    q.wait(job)
    assert job.result == "fine" and not job.failed


def test_job_failure_records_error():
    q = Queue()

    def bad():
        raise ValueError("nope")
    job = q.submit(bad)
    q.wait(job)
    assert job.failed and "ValueError" in job.error and job.traceback


def test_task_decorator_uses_queue():
    q = Queue()

    @task(retries=0, queue=q)
    def gen(n):
        return n + 1
    job = gen(1)
    q.wait(job)
    assert job.result == 2
    assert gen.sync(1) == 2


def test_cache_ttl_tags_invalidate():
    now = [1000.0]
    c = MemoryCache(time_fn=lambda: now[0])
    c.set("a", 1, ttl=10, tags=["t"])
    assert c.get("a") == (1, True)
    now[0] += 20
    assert c.get("a") == (None, False)
    c.set("b", 2, tags=["t2"])
    c.invalidate_tag("t2")
    assert c.get("b") == (None, False)


def test_cache_decorator_memoizes():
    calls = []

    @cache(minutes=5)
    def f(x):
        calls.append(x)
        return x * 2
    assert f(3) == 6 and f(3) == 6 and len(calls) == 1
    f.cache_invalidate()
    f(3)
    assert len(calls) == 2


def test_swr_fresh_and_stale():
    now = [0.0]
    store = MemoryCache(time_fn=lambda: now[0])
    v, st = swr(store, "k", lambda: "v1", ttl=10, stale=100)
    assert (v, st) == ("v1", "miss")
    now[0] += 5
    assert swr(store, "k", lambda: "v2", ttl=10, stale=100)[1] == "fresh"
    now[0] += 20
    v, st = swr(store, "k", lambda: "v2", ttl=10, stale=100)
    assert (v, st) == ("v2", "revalidated")


def test_swr_loader_failure_serves_stale():
    now = [0.0]
    store = MemoryCache(time_fn=lambda: now[0])
    swr(store, "k", lambda: "old", ttl=10, stale=100)
    now[0] += 20

    def boom():
        raise RuntimeError("down")
    assert swr(store, "k", boom, ttl=10, stale=100) == ("old", "stale")


def test_channel_pubsub_and_presence():
    ch = Channel("chat")
    got = []
    unsub = ch.subscribe(got.append)
    assert ch.publish("hi") == 1 and got == ["hi"]
    unsub()
    assert ch.publish("x") == 0
    ch.join("u1", {"name": "a"})
    assert ch.members() == {"u1": {"name": "a"}}
    ch.leave("u1")
    assert ch.members() == {}


def test_bus_table_fanout():
    bus = Bus()
    got = []
    bus.channel("db:todos").subscribe(got.append)
    assert bus.notify_table("todos", {"id": 1}) == 1
    assert got[0]["table"] == "todos"


def test_realtime_marker():
    @realtime(channel="room")
    def chat():
        pass
    assert chat.__pyweb_realtime__ == "room"


def test_job_raising_typeerror_runs_once():
    from pyweb.jobs import Queue
    calls = []

    def bad():
        calls.append(1)
        raise TypeError("bug inside the job")

    q = Queue()
    job = q.submit(bad)
    q.wait(job, timeout=2)
    assert calls == [1] and job.failed and "TypeError" in job.error


def test_queue_bounds_threads_and_forgets_old_jobs():
    import threading
    import time
    q = Queue(workers=2, keep_seconds=0.05)
    running, peak, lock = [0], [0], threading.Lock()

    def work(i):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.01)
        with lock:
            running[0] -= 1
        return i

    batch = [q.submit(work, i) for i in range(20)]
    for job in batch:
        assert q.wait(job, timeout=5).result == job.result
    assert peak[0] <= 2                                   # never more than `workers` at once
    time.sleep(0.06)
    q.submit(work, 99)
    assert len(q.jobs) == 1                               # the 20 finished jobs were forgotten


def test_wait_returns_as_soon_as_the_job_finishes():
    import time
    q = Queue()
    job = q.submit(lambda: "ok")
    start = time.monotonic()
    assert q.wait(job).result == "ok" and time.monotonic() - start < 1


def test_memory_cache_forgets_expired_entries_nobody_reads():
    from pyweb.cache import MemoryCache
    now = [0.0]
    c = MemoryCache(time_fn=lambda: now[0])
    for i in range(255):
        c.set(f"page:{i}", i, ttl=10, tags=("pages",))
    now[0] = 11
    c.set("fresh", 1, ttl=10)                       # the 256th set sweeps
    assert list(c._store) == ["fresh"] and c._tags == {}
