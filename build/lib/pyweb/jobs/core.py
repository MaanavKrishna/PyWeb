"""Durable jobs: definitions, enqueueing, and what a running job can see.

::

    @app.job(retries=5, timeout=120)
    def send_invoice(order_id: int):
        ...

    send_invoice.enqueue(42)                       # runs soon, on any worker
    send_invoice.enqueue(42, delay=600)            # in ten minutes
    send_invoice.enqueue(42, key="invoice:42")     # at most one queued/running job with this key

Enqueueing inside a server function joins its transaction: if the function
fails and its writes roll back, the job is never queued (the "outbox"
pattern). Arguments must be JSON (numbers, strings, lists, dicts, ...).
"""

from __future__ import annotations

import contextvars
import datetime as dt
import hashlib
import json
import os
import random
import threading
import time

#: Every job definition by name (what a worker runs).
REGISTRY: dict = {}
#: Schedules: name -> (Cron | Every, job name, catchup).
SCHEDULES: dict = {}

STATES = ("queued", "running", "done", "failed", "dead")
_current: contextvars.ContextVar = contextvars.ContextVar("pyweb_job", default=None)
_backend = None
_lock = threading.Lock()


class JobError(Exception):
    pass


def backoff_delay(attempt, base=2.0, cap=3600.0, rng=random):
    """Seconds before retry ``attempt`` (1, 2, ...): "full jitter", spreading retries out."""
    return rng.uniform(0, min(cap, base * 2 ** (attempt - 1)))


class JobDef:
    def __init__(self, fn, *, name=None, retries=3, timeout=300, queue="default", unique_for=None,
                 backoff=2.0, max_backoff=3600.0):
        self.fn = fn
        self.name = name or fn.__name__
        self.retries = int(retries)
        self.timeout = float(timeout)
        self.queue = queue
        self.unique_for = unique_for
        self.backoff = float(backoff)
        self.max_backoff = float(max_backoff)
        self.__name__ = self.name
        self.__doc__ = fn.__doc__
        self.__wrapped__ = fn
        self.__pyweb_location__ = "worker"

    def __call__(self, *args, **kwargs):
        """Calling a job function runs it right here (like any function)."""
        return self.fn(*args, **kwargs)

    def run(self, *args, **kwargs):
        return self.fn(*args, **kwargs)

    def enqueue(self, *args, delay=None, at=None, key=None, **kwargs):
        """Queue a run with these arguments. Returns a :class:`JobHandle`."""
        payload = {"args": list(args), "kwargs": kwargs}
        try:
            text = json.dumps(payload, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise JobError(f"{self.name}: job arguments must be JSON ({exc}); pass ids, not objects") from None
        now = time.time()
        run_at = now
        if at is not None:
            run_at = at.timestamp() if isinstance(at, dt.datetime) else float(at)
        elif delay:
            run_at = now + (delay.total_seconds() if isinstance(delay, dt.timedelta) else float(delay))
        unique_until = None
        if key is None and self.unique_for:
            key = f"{self.name}:" + hashlib.sha256(text.encode()).hexdigest()[:24]
        if key is not None and self.unique_for:
            unique_until = now + float(self.unique_for)
        job_id = backend().enqueue(name=self.name, queue=self.queue, payload=text, run_at=run_at,
                                   max_attempts=self.retries + 1, timeout=self.timeout,
                                   unique_key=key, unique_until=unique_until)
        return JobHandle(job_id)


class JobHandle:
    """A queued job: ``status()``, ``wait()``."""

    def __init__(self, job_id):
        self.id = job_id

    def status(self):
        return backend().get(self.id)

    def wait(self, timeout=10.0, poll=0.05):
        """Wait until the job finished (done or dead); returns its record."""
        end = time.monotonic() + timeout
        while True:
            rec = self.status()
            if rec and rec["state"] in ("done", "dead"):
                return rec
            if time.monotonic() > end:
                raise TimeoutError(f"job {self.id} still {rec and rec['state']}")
            time.sleep(poll)

    def __repr__(self):
        return f"<job {self.id}>"


def job(fn=None, **options):
    """Make ``fn`` a durable job (``@job`` or ``@job(retries=5, ...)``)."""
    def deco(f):
        d = JobDef(f, **options)
        REGISTRY[d.name] = d
        return d
    return deco(fn) if fn is not None else deco


def cron(expr, *, tz="UTC", catchup="latest", name=None, **options):
    """Run the decorated function on a schedule: ``@cron("0 3 * * *", tz="Europe/London")``.

    ``catchup``: after downtime, run the most recent missed slot (``"latest"``,
    the default), every missed slot (``"all"``, at most 100) or none (``"none"``).
    Every server can run the scheduler: each slot is queued exactly once.
    """
    from .schedule import Cron
    sched = Cron(expr, tz)
    return _schedule(sched, catchup, name, options)


def every(*, seconds=0, minutes=0, hours=0, catchup="latest", name=None, **options):
    """Run the decorated function at a fixed interval: ``@every(minutes=5)``."""
    from .schedule import Every
    sched = Every(seconds + 60 * minutes + 3600 * hours)
    return _schedule(sched, catchup, name, options)


def _schedule(sched, catchup, name, options):
    if catchup not in ("latest", "all", "none"):
        raise ValueError('catchup must be "latest", "all" or "none"')

    def deco(f):
        d = f if isinstance(f, JobDef) else JobDef(f, name=name, **options)
        REGISTRY[d.name] = d
        SCHEDULES[d.name] = (sched, d.name, catchup)
        return d
    return deco


class RunContext:
    """What a running job can see: ``pyweb.jobs.current()``."""

    def __init__(self, job_id, name, attempt, max_attempts, backend_):
        self.id, self.name, self.attempt, self.max_attempts = job_id, name, attempt, max_attempts
        self.cancelled = threading.Event()
        self._backend = backend_

    def progress(self, value):
        """Record progress (0 to 1), visible in the job's record (and live queries on it)."""
        self._backend.progress(self.id, max(0.0, min(1.0, float(value))))

    @property
    def last_attempt(self):
        return self.attempt >= self.max_attempts


def current():
    """The running job's :class:`RunContext`, or None outside a job."""
    return _current.get()


def progress(value):
    ctx = current()
    if ctx is not None:
        ctx.progress(value)


# ------------------------------------------------------------- backends

_chosen = False                 # use_backend() was called: never switch automatically


def use_backend(b):
    """Store jobs in ``b`` (a backend object); None goes back to choosing automatically."""
    global _backend, _chosen
    _backend = b
    _chosen = b is not None
    return b


def _stale(b):
    """An automatically chosen store that no longer matches the app's database (another app loaded)."""
    if _chosen or b is None:
        return False
    from pyweb import models as M
    from .db import DatabaseBackend
    from .memory import MemoryBackend
    db = M.database()
    if isinstance(b, DatabaseBackend):
        return db is not b.db
    return isinstance(b, MemoryBackend) and db is not None and os.environ.get("PYWEB_JOBS", "") in ("", "db")


def backend():
    """The job store: ``PYWEB_JOBS`` (``db``, ``redis`` or ``memory``), else the app's database
    when there is one, else memory."""
    global _backend
    if _backend is not None and not _stale(_backend):
        return _backend
    with _lock:
        if _backend is not None and not _stale(_backend):
            return _backend
        _backend = None
        kind = os.environ.get("PYWEB_JOBS", "").strip().lower()
        if kind == "redis":
            from .redis import RedisBackend
            url = os.environ.get("PYWEB_REDIS_URL")
            if not url:
                raise JobError("PYWEB_JOBS=redis needs PYWEB_REDIS_URL")
            _backend = RedisBackend(url)
        elif kind in ("", "db"):
            from pyweb import models as M
            db = M.database()
            if db is not None:
                from .db import DatabaseBackend
                _backend = DatabaseBackend(db)
            elif kind == "db":
                raise JobError("PYWEB_JOBS=db but the app has no database (App(database=...) or DATABASE_URL)")
        elif kind != "memory":
            raise JobError(f"PYWEB_JOBS={kind!r}: use db, redis or memory")
        if _backend is None:
            from .memory import MemoryBackend
            _backend = MemoryBackend()
        return _backend


def durable():
    """Whether jobs survive restarts (a database or Redis store)."""
    from .memory import MemoryBackend
    return not isinstance(backend(), MemoryBackend)


def retry(job_id):
    """Queue a failed or dead job again (now, with a fresh set of attempts)."""
    return backend().retry(job_id)
