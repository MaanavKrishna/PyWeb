"""Redis backplane: RedisBus streams, RedisQueue drain, job save/load."""

import json

from pyweb import jobs as _jobs
from pyweb import realtime as _rt


class FakeRedis:
    def __init__(self):
        self.streams = {}
        self.pubs = []
        self.hashes = {}
        self.lists = {}
        self._n = 0

    def incr(self, key):
        self.counters = getattr(self, "counters", {})
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def xadd(self, key, fields, id="*", maxlen=None, approximate=None):
        self._n += 1
        sid = id if id != "*" else f"{self._n}-0"
        self.streams.setdefault(key, []).append(
            (sid.encode(), {k.encode(): v.encode() for k, v in fields.items()}))
        return sid

    def lpop(self, key):
        lst = self.lists.get(key, [])
        return lst.pop(0) if lst else None

    def publish(self, key, body):
        self.pubs.append((key, body))
        return 1

    def xrange(self, key, min="-", max="+", count=None):
        entries = self.streams.get(key, [])
        if isinstance(min, str) and min.startswith("("):
            floor = int(min[1:].split("-")[0])
            entries = [e for e in entries if int(e[0].decode().split("-")[0]) > floor]
        return entries[:count] if count else entries

    def hset(self, key, mapping=None, **kw):
        h = self.hashes.setdefault(key, {})
        for k, v in dict(mapping or {}, **kw).items():
            h[k.encode() if isinstance(k, str) else k] = (
                str(v).encode() if not isinstance(v, bytes) else v)
        return 1

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def rpush(self, key, *vals):
        self.lists.setdefault(key, []).extend(vals)
        return len(self.lists[key])

    def blpop(self, key, timeout=0):
        lst = self.lists.get(key, [])
        if not lst:
            return None
        return (key.encode(), lst.pop(0).encode()
                if isinstance(lst[0], str) else lst.pop(0))


def test_redis_bus_publishes_to_stream_and_pubsub():
    r = FakeRedis()
    bus = _rt.RedisBus(client=r)
    bus.publish("chat", {"text": "hi"})
    assert "pyweb:stream:chat" in r.streams
    assert r.pubs and r.pubs[0][0] == "pyweb:live:chat"


def test_redis_bus_since_merges_remote_history():
    r = FakeRedis()
    a = _rt.RedisBus(client=r)
    b = _rt.RedisBus(client=r)  # second process, empty local log
    a.publish("room", {"n": 1})
    a.publish("room", {"n": 2})
    msgs = b.since("room", 0)
    assert [m["n"] for _, m in msgs] == [1, 2]
    assert b.since("room", msgs[0][0])[0][1] == {"n": 2}  # resume works


def test_redis_bus_degrades_when_redis_down():
    class Dead:
        def __getattr__(self, _):
            raise ConnectionError("down")
    bus = _rt.RedisBus(client=Dead())
    got = []
    bus.channel("c").subscribe(got.append)
    assert bus.publish("c", "m") == 1  # local subs still fire
    assert got == ["m"]
    assert bus.since("c", 0)  # local log still readable


def test_redis_bus_history_newest_first():
    r = FakeRedis()
    bus = _rt.RedisBus(client=r)
    bus.publish("h", "a")
    bus.publish("h", "b")
    assert [m for _, m in bus.history("h")] == ["b", "a"]


def test_redis_queue_submit_and_drain():
    r = FakeRedis()
    q = _jobs.RedisQueue(client=r)
    def add(a, b):
        return a + b
    t = _jobs.task(queue=q)(add)
    job = t(2, 3)
    assert job.pending  # not run yet: waiting in Redis list
    done = q.drain()
    assert done is not None and not done.pending
    assert done.result == 5


def test_redis_queue_unknown_fn_marks_failed():
    r = FakeRedis()
    q = _jobs.RedisQueue(client=r)
    q._r.rpush(q._prefix + "pending", "ghost")
    q._r.hset(q._prefix + "job:ghost", mapping={
        "fn": "missing", "payload": json.dumps({"args": [], "kwargs": {}}),
        "retries": 0})
    done = q.drain()
    assert done.failed and "unknown function" in done.error


def test_job_save_load_roundtrip(tmp_path):
    q = _jobs.Queue()
    def f():
        return 42
    job = q.submit(f)
    q.wait(job)
    assert job.result == 42
    path = str(tmp_path / "jobs.json")
    q.save(path)
    q2 = _jobs.Queue()
    assert q2.load(path) == 1
    assert q2.get(job.id).result == 42
    assert q2.load(str(tmp_path / "missing.json")) == 0
