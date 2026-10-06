"""Jobs in Redis (``PYWEB_JOBS=redis``): for apps whose database shouldn't carry the queue.

Each queue is a sorted set scored by run time (delays and retries are just
later scores); running jobs sit in a sorted set scored by lease expiry.
Claiming, finishing and failing are Lua scripts, so they're atomic however
many workers run. An expired lease puts the job back in its queue.

Outbox: enqueueing inside a database transaction waits for the commit, so
a rolled-back request queues nothing. (A crash between the commit and the
enqueue can lose that one job; the database backend has no such gap.)
"""

from __future__ import annotations

import json
import time

from .memory import Backend, new_id, split_payload, when_committed

PREFIX = "pyweb:jobs:v5:"

ENQUEUE = """
local p, id, key = ARGV[1], ARGV[2], ARGV[3]
local now = tonumber(ARGV[4])
if key ~= '' then
  local existing = redis.call('GET', p..'uniq:'..key)
  if existing then
    local st = redis.call('HMGET', p..'job:'..existing, 'state', 'unique_until')
    local final = st[1] == 'done' or st[1] == 'dead' or not st[1]
    local held = st[2] and st[2] ~= '' and tonumber(st[2]) > now
    if not final or held then return existing end
  end
  redis.call('SET', p..'uniq:'..key, id)
end
for i = 7, #ARGV, 2 do redis.call('HSET', p..'job:'..id, ARGV[i], ARGV[i+1]) end
redis.call('ZADD', p..'q:'..ARGV[5], tonumber(ARGV[6]), id)
redis.call('ZADD', p..'all', now, id)
return id
"""

CLAIM = """
local p, token = ARGV[1], ARGV[2]
local now, limit = tonumber(ARGV[3]), tonumber(ARGV[4])
for _, id in ipairs(redis.call('ZRANGEBYSCORE', p..'running', '-inf', '('..now)) do
  redis.call('ZREM', p..'running', id)
  local q = redis.call('HGET', p..'job:'..id, 'queue')
  if q then
    redis.call('HSET', p..'job:'..id, 'state', 'queued', 'locked_by', '')
    redis.call('ZADD', p..'q:'..q, now, id)
  end
end
local out = {}
for i = 5, #ARGV do
  if #out >= limit then break end
  for _, id in ipairs(redis.call('ZRANGEBYSCORE', p..'q:'..ARGV[i], '-inf', now, 'LIMIT', 0, limit - #out)) do
    redis.call('ZREM', p..'q:'..ARGV[i], id)
    local timeout = tonumber(redis.call('HGET', p..'job:'..id, 'timeout') or '300')
    local until_ = now + timeout + 30
    redis.call('ZADD', p..'running', until_, id)
    redis.call('HINCRBY', p..'job:'..id, 'attempts', 1)
    redis.call('HSET', p..'job:'..id, 'state', 'running', 'locked_by', token, 'locked_until', until_)
    table.insert(out, id)
  end
end
return out
"""

# ARGV: prefix, id, token, outcome (done|dead|retry|release|extend), now, extra (result/error/time), extra2
FINISH = """
local p, id, token, outcome = ARGV[1], ARGV[2], ARGV[3], ARGV[4]
local now = tonumber(ARGV[5])
local k = p..'job:'..id
local st = redis.call('HMGET', k, 'state', 'locked_by', 'queue', 'unique_key', 'unique_until', 'attempts')
if st[1] ~= 'running' or st[2] ~= token then return 0 end
if outcome == 'extend' then
  redis.call('ZADD', p..'running', tonumber(ARGV[6]), id)
  redis.call('HSET', k, 'locked_until', ARGV[6])
  return 1
end
redis.call('ZREM', p..'running', id)
if outcome == 'retry' or outcome == 'release' then
  local at = now
  if outcome == 'retry' then
    at = tonumber(ARGV[7])
    redis.call('HSET', k, 'last_error', ARGV[6])
  else
    local a = tonumber(st[6] or '1') - 1
    if a < 0 then a = 0 end
    redis.call('HSET', k, 'attempts', a)
  end
  redis.call('HSET', k, 'state', 'queued', 'locked_by', '', 'locked_until', '', 'run_at', at)
  redis.call('ZADD', p..'q:'..st[3], at, id)
  return 1
end
if outcome == 'done' then
  redis.call('HSET', k, 'state', 'done', 'result', ARGV[6], 'progress', 1)
else
  redis.call('HSET', k, 'state', 'dead', 'last_error', ARGV[6])
end
redis.call('HSET', k, 'locked_by', '', 'locked_until', '', 'finished_at', now)
if st[4] and st[4] ~= '' and (not st[5] or st[5] == '') then
  if redis.call('GET', p..'uniq:'..st[4]) == id then redis.call('DEL', p..'uniq:'..st[4]) end
end
return 1
"""

FIELDS = ("id", "name", "queue", "payload", "state", "attempts", "max_attempts", "timeout", "run_at",
          "locked_by", "locked_until", "unique_key", "unique_until", "progress", "last_error", "result",
          "created_at", "finished_at")


class RedisBackend(Backend):
    def __init__(self, url=None, *, client=None, prefix=PREFIX):
        super().__init__()
        if client is None:
            import redis
            client = redis.Redis.from_url(url)
        self.r = client
        self.p = prefix
        self._enqueue = self.r.register_script(ENQUEUE)
        self._claim = self.r.register_script(CLAIM)
        self._finish = self.r.register_script(FINISH)

    def enqueue(self, *, name, queue, payload, run_at, max_attempts, timeout, unique_key=None, unique_until=None):
        job_id = new_id()

        def push():
            now = time.time()
            fields = {"id": job_id, "name": name, "queue": queue, "payload": payload, "state": "queued",
                      "attempts": 0, "max_attempts": max_attempts, "timeout": timeout, "run_at": run_at,
                      "unique_key": unique_key or "", "unique_until": "" if unique_until is None else unique_until,
                      "progress": 0, "created_at": now}
            flat = [x for kv in fields.items() for x in (kv[0], str(kv[1]))]
            got = self._enqueue(args=[self.p, job_id, unique_key or "", now, queue, run_at, *flat])
            self.notify(queue)
            try:
                from pyweb.realtime import current_bus
                current_bus().publish("pyweb.jobs", queue)
            except Exception:  # noqa: BLE001
                pass
            return got.decode() if isinstance(got, bytes) else got

        holder = []
        when_committed(lambda: holder.append(push()))   # outbox: only once the request's writes commit
        return holder[0] if holder else job_id

    def claim(self, queues, worker, limit, now=None):
        if limit <= 0 or not queues:
            return []
        now = time.time() if now is None else now
        token = f"{worker}:{new_id()[:8]}"[:80]
        ids = self._claim(args=[self.p, token, now, int(limit), *queues])
        out = []
        for raw in ids:
            job_id = raw.decode() if isinstance(raw, bytes) else raw
            rec = self._hash(job_id)
            if rec is None:
                continue
            out.append({"id": job_id, "name": rec["name"], "payload": rec["payload"], "attempt": int(rec["attempts"]),
                        "max_attempts": int(rec["max_attempts"]), "timeout": float(rec["timeout"]), "token": token,
                        "queue": rec["queue"]})
        return out

    def _outcome(self, job_id, token, outcome, *extra):
        return bool(self._finish(args=[self.p, job_id, token, outcome, time.time(), *extra]))

    def complete(self, job_id, token, result=None):
        return self._outcome(job_id, token, "done", json.dumps(result, default=str))

    def fail(self, job_id, token, error, retry_at=None):
        if retry_at is None:
            return self._outcome(job_id, token, "dead", error[:4000])
        return self._outcome(job_id, token, "retry", error[:4000], retry_at)

    def extend(self, job_id, token, until):
        return self._outcome(job_id, token, "extend", until)

    def release(self, job_id, token):
        return self._outcome(job_id, token, "release")

    def progress(self, job_id, value):
        self.r.hset(self.p + "job:" + job_id, "progress", value)

    def _hash(self, job_id):
        raw = self.r.hgetall(self.p + "job:" + job_id)
        if not raw:
            return None
        return {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                for k, v in raw.items()}

    def _public(self, rec):
        def num(key, cast=float):
            v = rec.get(key)
            return cast(float(v)) if v not in (None, "") else None
        return {"id": rec["id"], "name": rec["name"], "queue": rec["queue"], "state": rec["state"],
                "attempts": num("attempts", int), "max_attempts": num("max_attempts", int),
                "run_at": num("run_at"), "progress": num("progress") or 0.0,
                "last_error": rec.get("last_error") or None, "created_at": num("created_at"),
                "finished_at": num("finished_at"), "unique_key": rec.get("unique_key") or None,
                "args": split_payload(rec.get("payload"))[0], "trace": split_payload(rec.get("payload"))[1],
                "result": json.loads(rec["result"]) if rec.get("result") else None}

    def get(self, job_id):
        rec = self._hash(job_id)
        return self._public(rec) if rec else None

    def list(self, state=None, limit=50, name=None):
        out = []
        for raw in self.r.zrevrange(self.p + "all", 0, 4999):
            rec = self._hash(raw.decode() if isinstance(raw, bytes) else raw)
            if rec and (state is None or rec["state"] == state) and (name is None or rec["name"] == name):
                out.append(self._public(rec))
                if len(out) >= limit:
                    break
        return out

    def counts(self):
        """Waiting (every queue this app uses) and running jobs: two cheap counts, no scan."""
        from . import core
        queues = sorted({d.queue for d in core.REGISTRY.values()} | {"default"})
        pipe = self.r.pipeline()
        for q in queues:
            pipe.zcard(self.p + "q:" + q)
        pipe.zcard(self.p + "running")
        *waiting, running = pipe.execute()
        return {"queued": int(sum(waiting)), "running": int(running)}

    def retry(self, job_id):
        rec = self._hash(job_id)
        if rec is None or rec["state"] not in ("dead", "failed"):
            return False
        now = time.time()
        self.r.hset(self.p + "job:" + job_id, mapping={"state": "queued", "run_at": now, "attempts": 0,
                                                       "finished_at": ""})
        self.r.zadd(self.p + "q:" + rec["queue"], {job_id: now})
        self.notify(rec["queue"])
        return True

    def purge(self, before):
        gone = 0
        now = time.time()
        for raw in self.r.zrange(self.p + "all", 0, 9999):
            job_id = raw.decode() if isinstance(raw, bytes) else raw
            rec = self._hash(job_id)
            if rec is None:
                self.r.zrem(self.p + "all", job_id)
                continue
            finished = float(rec.get("finished_at") or 0)
            held = rec.get("unique_until") and float(rec["unique_until"]) > now
            if rec["state"] in ("done", "dead") and finished and finished < before and not held:
                self.r.delete(self.p + "job:" + job_id)
                self.r.zrem(self.p + "all", job_id)
                gone += 1
        return gone

    def mark(self, name):
        v = self.r.hget(self.p + "marks", name)
        return float(v) if v is not None else None

    def set_mark(self, name, slot):
        # Only ever moves forward (another scheduler may have marked a later slot).
        self.r.eval("local c = redis.call('HGET', KEYS[1], ARGV[1]) "
                    "if not c or tonumber(c) < tonumber(ARGV[2]) then redis.call('HSET', KEYS[1], ARGV[1], ARGV[2]) end",
                    1, self.p + "marks", name, slot)
