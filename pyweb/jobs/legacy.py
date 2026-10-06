"""0.4 background jobs: @task, an in-memory queue, a basic RedisQueue (kept for compatibility)."""

from __future__ import annotations

import threading
import time
import traceback
import uuid


class Job:
    def __init__(self, job_id, fn_name):
        self.id = job_id
        self.fn_name = fn_name
        self.pending = True
        self.failed = False
        self.error = None
        self.result = None
        self.progress = 0.0
        self.traceback = None
        self.finished_at = None
        self.done = threading.Event()

    def finish(self):
        self.pending = False
        self.finished_at = time.monotonic()
        self.done.set()

    def set_progress(self, value):
        self.progress = max(0.0, min(1.0, float(value)))

    def to_dict(self):
        return {"id": self.id, "fn": self.fn_name, "pending": self.pending,
                "failed": self.failed, "error": self.error,
                "result": self.result, "progress": self.progress}


class Queue:
    """In-memory queue (Redis/RabbitMQ/Kafka adapters plug in here).

    Jobs run on up to ``workers`` threads; more wait their turn. Finished
    jobs are kept for ``keep_seconds`` so callers can read their result,
    then forgotten.
    """

    def __init__(self, workers=8, keep_seconds=3600):
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self.workers = workers
        self.keep_seconds = keep_seconds
        self._pool = None

    def submit(self, fn, *args, retries=0, **kwargs):
        import concurrent.futures
        job = Job(uuid.uuid4().hex[:12], getattr(fn, "__name__", "fn"))
        with self._lock:
            self._forget_old()
            self.jobs[job.id] = job
            if self._pool is None:
                self._pool = concurrent.futures.ThreadPoolExecutor(self.workers, thread_name_prefix="pyweb-job")
        self._pool.submit(self._run, job, fn, args, kwargs, retries)
        return job

    def shutdown(self, timeout=10.0):
        """Stop taking jobs and wait up to ``timeout`` seconds for running ones to finish.

        Returns how many were still running when time ran out.
        """
        import time as _time
        end = _time.monotonic() + timeout
        with self._lock:
            pending = [j for j in self.jobs.values() if j.pending]
        for job in pending:
            job.done.wait(max(0.0, end - _time.monotonic()))
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None
        return sum(1 for j in pending if j.pending)

    def _forget_old(self):
        """Drop jobs that finished more than ``keep_seconds`` ago. Hold the lock."""
        cutoff = time.monotonic() - self.keep_seconds
        for jid in [j for j, job in self.jobs.items() if job.finished_at is not None and job.finished_at < cutoff]:
            del self.jobs[jid]

    @staticmethod
    def _wants_job(fn):
        """Does ``fn`` accept the ``_job`` keyword (for progress reporting)?"""
        import inspect
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return False
        return "_job" in params or any(p.kind is p.VAR_KEYWORD for p in params.values())

    def _run(self, job, fn, args, kwargs, retries):
        attempt = 0
        call_kwargs = dict(kwargs)
        if self._wants_job(fn):
            call_kwargs["_job"] = job
        while True:
            try:
                job.result = fn(*args, **call_kwargs)
                job.finish()
                return
            except Exception as exc:  # noqa: BLE001
                err = exc
                tb = traceback.format_exc()
            if attempt >= retries:
                job.failed = True
                job.error = f"{type(err).__name__}: {err}"
                job.traceback = tb
                job.finish()
                return
            attempt += 1
            time.sleep(0.01 * attempt)

    def get(self, job_id):
        return self.jobs.get(job_id)

    def wait(self, job, timeout=10.0):
        if job.pending:
            job.done.wait(timeout)
        return job


    def save(self, path):
        """Persist job records to JSON (survives restarts; payload fns excluded)."""
        import json as _json
        with self._lock:
            data = {jid: j.to_dict() for jid, j in self.jobs.items()}
        with open(path, "w") as fh:
            _json.dump(data, fh)

    def load(self, path):
        """Restore job records saved with :meth:`save`."""
        import json as _json
        try:
            with open(path) as fh:
                data = _json.load(fh)
        except FileNotFoundError:
            return 0
        with self._lock:
            for jid, rec in data.items():
                job = Job(jid, rec.get("fn", "?"))
                job.pending = bool(rec.get("pending", False))
                job.failed = bool(rec.get("failed", False))
                job.error = rec.get("error")
                job.result = rec.get("result")
                job.progress = float(rec.get("progress", 0.0))
                if not job.pending:
                    job.finish()
                self.jobs[jid] = job
        return len(data)


class RedisQueue(Queue):
    """Cross-process job queue over Redis lists + hashes.

    ``submit`` pushes the job id onto ``<prefix>pending`` and stores the
    record in ``<prefix>job:<id>``; any process running :meth:`drain`
    (or a worker loop) can pick it up. Payloads must be JSON-serializable
    args; the function itself is resolved from a registry so workers do
    not need the submitting process's memory. Falls back to in-memory
    execution when Redis is unreachable.
    """

    def __init__(self, url="redis://localhost:6379/0", client=None,
                 prefix="pyweb:jobs:"):
        super().__init__()
        self._prefix = prefix
        self._registry: dict[str, object] = {}
        if client is not None:
            self._r = client
        else:
            try:
                import redis
            except ImportError as e:
                raise RuntimeError(
                    "RedisQueue requires the 'redis' package: "
                    "pip install redis") from e
            self._r = redis.Redis.from_url(url)

    def register(self, fn):
        self._registry[getattr(fn, "__name__", "fn")] = fn
        return fn

    def submit(self, fn, *args, retries=0, **kwargs):
        import json as _json
        name = getattr(fn, "__name__", "fn")
        self._registry.setdefault(name, fn)
        try:
            payload = _json.dumps({"args": args, "kwargs": kwargs})
        except (TypeError, ValueError):
            return super().submit(fn, *args, retries=retries, **kwargs)
        job = Job(__import__("uuid").uuid4().hex[:12], name)
        with self._lock:
            self.jobs[job.id] = job
        try:
            self._r.hset(self._prefix + f"job:{job.id}", mapping={
                "fn": name, "payload": payload, "retries": retries})
            self._r.rpush(self._prefix + "pending", job.id)
        except Exception:  # noqa: BLE001 — Redis down: run in-memory
            return super().submit(fn, *args, retries=retries, **kwargs)
        return job

    def drain(self, timeout=0):
        """Run one pending job from the Redis list. Returns job or None."""
        import json as _json
        try:
            if timeout:
                item = self._r.blpop(self._prefix + "pending", timeout=timeout)
                job_id = item[1] if item else None
            else:
                # BLPOP with timeout=0 blocks forever on real Redis.
                job_id = self._r.lpop(self._prefix + "pending")
        except Exception:
            return None
        if not job_id:
            return None
        job_id = job_id.decode() if isinstance(job_id, bytes) else job_id
        try:
            rec = self._r.hgetall(self._prefix + f"job:{job_id}")
            get = lambda k: (rec.get(k.encode(), b"") or b"").decode() \
                if isinstance(rec.get(k.encode(), b""), bytes) \
                else rec.get(k, "")
            fn = self._registry.get(get("fn"))
            payload = _json.loads(get("payload") or "{}")
            retries = int(get("retries") or 0)
        except Exception:  # noqa: BLE001
            return self.jobs.get(job_id)
        job = self.jobs.get(job_id)
        if job is None:
            job = Job(job_id, get("fn"))
            with self._lock:
                self.jobs[job_id] = job
        if fn is None:
            job.failed = True
            job.error = "unknown function for worker"
            job.finish()
            return job
        self._run(job, fn, payload.get("args", ()),
                  payload.get("kwargs", {}), retries)
        try:
            try:
                result = _json.dumps(job.result)
            except (TypeError, ValueError):
                result = _json.dumps(repr(job.result))
            self._r.hset(self._prefix + f"job:{job_id}", mapping={
                "done": "1", "failed": "1" if job.failed else "",
                "result": result, "error": str(job.error or "")})
        except Exception:
            pass
        return job

    def status(self, job_id):
        """Job state as stored in Redis (readable from any process)."""
        import json as _json
        rec = self._r.hgetall(self._prefix + f"job:{job_id}")
        if not rec:
            return None
        rec = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
               for k, v in rec.items()}
        return {"fn": rec.get("fn"), "done": rec.get("done") == "1",
                "failed": rec.get("failed") == "1",
                "result": _json.loads(rec["result"]) if rec.get("result") else None,
                "error": rec.get("error") or None}


_default_queue = Queue()


def task(_fn=None, *, retries=0, queue=None):
    q = queue or _default_queue

    def deco(fn):
        if isinstance(q, RedisQueue):
            q.register(fn)

        def wrapper(*args, **kwargs):
            return q.submit(fn, *args, retries=retries, **kwargs)
        wrapper.__pyweb_location__ = "worker"
        wrapper.sync = fn
        wrapper.queue = q
        return wrapper
    return deco(_fn) if _fn else deco
