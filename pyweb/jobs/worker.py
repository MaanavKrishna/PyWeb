"""Workers run queued jobs; the scheduler queues cron and interval jobs.

``pyweb worker app.pyweb`` runs one; ``pyweb dev`` and ``pyweb serve`` run one
inside each server process unless ``PYWEB_WORKER=0`` (then run ``pyweb
worker`` processes yourself).

Each job runs on its own thread, in a transaction (like a server function),
with a time limit: a job over its ``timeout`` is abandoned and counts as a
failed attempt (Python can't stop a thread, so the job should check
``pyweb.jobs.current().cancelled`` if it loops for long). Failed attempts
are retried after a random delay that grows with each attempt; after the
last one the job is ``dead`` (see /admin or ``pyweb jobs list --dead``).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import socket
import threading
import time
import traceback
import weakref

from pyweb import telemetry

from . import core

log = logging.getLogger("pyweb.jobs")
UTC = dt.timezone.utc
SCHEDULE_EVERY = 10.0          # seconds between scheduler ticks
CRON_KEY_DAYS = 2              # a schedule slot's key blocks duplicates this long

WORKERS: "weakref.WeakSet[Worker]" = weakref.WeakSet()


class _LostLease(Exception):
    pass


def _shares_app_database(b):
    from pyweb import models as M
    from .db import DatabaseBackend
    return isinstance(b, DatabaseBackend) and b.db is M.database()


class _Running:
    __slots__ = ("claim", "ctx", "thread", "deadline")

    def __init__(self, claim, ctx, thread, deadline):
        self.claim, self.ctx, self.thread, self.deadline = claim, ctx, thread, deadline


class Worker:
    def __init__(self, backend=None, *, queues=None, concurrency=4, schedule=True, poll=1.0, name=None,
                 heartbeat=None):
        self._backend = backend
        self.heartbeat = heartbeat            # a file touched after every healthy round (container health checks)
        self.queues = tuple(queues) if queues else None      # None: every queue this app's jobs use
        self.concurrency = max(1, int(concurrency))
        self.schedule = schedule
        self.poll = poll
        self.name = name or f"{socket.gethostname()}:{os.getpid()}"
        self.running: dict[str, _Running] = {}
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.thread = None
        self.started_at = time.time()
        self._next_schedule = 0.0
        self._unsub = None
        WORKERS.add(self)

    @property
    def backend(self):
        return self._backend or core.backend()

    def queue_names(self):
        if self.queues:
            return list(self.queues)
        return sorted({d.queue for d in core.REGISTRY.values()} | {"default"})

    # ------------------------------------------------------------- lifecycle
    def start(self):
        b = self.backend
        b.listeners.append(self._on_enqueue)
        try:
            from pyweb.realtime import current_bus
            self._unsub = current_bus().channel("pyweb.jobs").subscribe(lambda _m: self.wake.set())
        except Exception:  # noqa: BLE001
            pass
        self.thread = threading.Thread(target=self._loop, name="pyweb-worker", daemon=True)
        self.thread.start()
        return self

    def _on_enqueue(self, _queue):
        self.wake.set()

    def stop(self, timeout=10.0):
        """Stop claiming, give running jobs up to ``timeout`` seconds, then hand the rest back."""
        self.stopping.set()
        self.wake.set()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                if not self.running:
                    break
            time.sleep(0.02)
        with self.lock:
            left = list(self.running.values())
            self.running.clear()
        for r in left:
            r.ctx.cancelled.set()
            try:
                self.backend.release(r.claim["id"], r.claim["token"])
            except Exception:  # noqa: BLE001
                pass
        try:
            self.backend.listeners.remove(self._on_enqueue)
        except ValueError:
            pass
        if self._unsub is not None:
            self._unsub()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(2)
        return len(left)

    def _loop(self):
        delay, failing = self.poll, None
        while not self.stopping.is_set():
            try:
                self.tick()
                if self.heartbeat:
                    touch(self.heartbeat)
                if failing is not None:
                    log.warning("job worker recovered")
                delay, failing = self.poll, None
            except Exception as exc:  # noqa: BLE001 - e.g. the database is unreachable or refuses
                text = f"{type(exc).__name__}: {exc}"
                if text != failing:
                    log.exception("job worker can't reach its job store (retrying with backoff)")
                failing = text
                delay = min(60.0, max(self.poll, delay * 2))
            self.wake.wait(delay)
            self.wake.clear()

    def tick(self, now=None):
        """One round: time out overdue jobs, queue scheduled ones, claim and start what fits."""
        now = time.time() if now is None else now
        self._expire(now)
        if self.schedule and now >= self._next_schedule:
            run_schedules(self.backend, now=now, started_at=self.started_at)
            self._next_schedule = min(now + SCHEDULE_EVERY, next_due(now))
        with self.lock:
            free = self.concurrency - len(self.running)
        if free <= 0 or self.stopping.is_set():
            return 0
        claims = self.backend.claim(self.queue_names(), self.name, free, now=now)
        for c in claims:
            self._start(c, now)
        return len(claims)

    def _start(self, claim, now):
        ctx = core.RunContext(claim["id"], claim["name"], claim["attempt"], claim["max_attempts"], self.backend)
        run = _Running(claim, ctx, None, now + claim["timeout"])
        thread = threading.Thread(target=self._execute, args=(run,), name=f"pyweb-job-{claim['name']}", daemon=True)
        run.thread = thread
        with self.lock:
            self.running[claim["id"]] = run
        thread.start()

    def _expire(self, now):
        with self.lock:
            overdue = [r for r in self.running.values() if r.deadline < now]
            for r in overdue:
                self.running.pop(r.claim["id"], None)
        for r in overdue:
            r.ctx.cancelled.set()
            telemetry.instruments.jobs_done.inc(job=r.claim["name"], outcome="timeout")
            self._failed(r.claim, f"TimeoutError: took longer than {r.claim['timeout']:g}s")

    # --------------------------------------------------------------- running
    def _execute(self, run):
        claim, b = run.claim, self.backend
        try:
            definition = core.REGISTRY.get(claim["name"])
            if definition is None:
                b.fail(claim["id"], claim["token"], f"no job named {claim['name']!r} in this app")
                return
            if claim["attempt"] > claim["max_attempts"]:
                b.fail(claim["id"], claim["token"],
                       "the worker running it stopped (its lease ran out) and it has no attempts left")
                return
            payload = json.loads(claim["payload"] or "{}")
            token = core._current.set(run.ctx)
            same_db = _shares_app_database(b)
            completed = False
            tel, tel_token = telemetry.tracing.begin("job", traceparent=payload.get("trace"), job=claim["name"],
                                                     route=f"job:{claim['name']}")
            outcome, failure = "done", None
            try:
                from pyweb.db import request_scope
                with request_scope():
                    result = definition.fn(*payload.get("args", ()), **payload.get("kwargs", {}))
                    if same_db:
                        # The job's writes and its "done" commit together: a crash can't leave work
                        # done but the job queued (which would run it twice).
                        if not self._still_mine(claim) or not b.complete(claim["id"], claim["token"], result):
                            raise _LostLease()
                        completed = True
                if not completed and not self._still_mine(claim):
                    outcome = "lost"                    # it ran past its timeout; that was counted then
            except _LostLease:
                outcome = "lost"
                return                                  # timed out or reclaimed: its writes rolled back
            except Exception as exc:  # noqa: BLE001
                failure = exc
                outcome = "retry" if claim["attempt"] < claim["max_attempts"] else "dead"
                if self._still_mine(claim):
                    self._failed(claim, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=20)}")
                if outcome == "dead":       # out of attempts: that's an error someone should see
                    telemetry.report_error(exc, "job", job=claim["name"], job_id=claim["id"],
                                           attempt=claim["attempt"])
                return
            finally:
                core._current.reset(token)
                telemetry.instruments.jobs_done.inc(job=claim["name"], outcome=outcome)
                telemetry.instruments.job_seconds.observe(tel.elapsed_ms / 1000.0, job=claim["name"])
                telemetry.tracing.end(tel_token, tel, status=outcome, error=failure)
            if not completed and self._still_mine(claim):
                b.complete(claim["id"], claim["token"], result)
        except Exception:  # noqa: BLE001 - never kill the worker
            log.exception("job %s crashed the runner", claim.get("id"))
        finally:
            with self.lock:
                if self.running.get(claim["id"]) is run:
                    del self.running[claim["id"]]
            self.wake.set()

    def _still_mine(self, claim):
        with self.lock:
            return claim["id"] in self.running            # not timed out or handed back meanwhile

    def _failed(self, claim, error):
        definition = core.REGISTRY.get(claim["name"])
        retry_at = None
        if claim["attempt"] < claim["max_attempts"]:
            base = definition.backoff if definition else 2.0
            cap = definition.max_backoff if definition else 3600.0
            retry_at = time.time() + core.backoff_delay(claim["attempt"], base, cap)
        log.warning("job %s (%s) attempt %s/%s failed%s: %s", claim["id"], claim["name"], claim["attempt"],
                    claim["max_attempts"], "" if retry_at else " for good", error.splitlines()[0])
        self.backend.fail(claim["id"], claim["token"], error, retry_at)

    # ----------------------------------------------------------------- tests
    def run_inline(self, limit=100):
        """Run due jobs one after another on this thread, without a thread pool (the playground
        in Pyodide, scripts). Returns how many ran."""
        ran = 0
        while ran < limit:
            now = time.time()
            claims = self.backend.claim(self.queue_names(), self.name, 1, now=now)
            if not claims:
                break
            claim = claims[0]
            run = _Running(claim, core.RunContext(claim["id"], claim["name"], claim["attempt"],
                                                  claim["max_attempts"], self.backend), None, now + claim["timeout"])
            with self.lock:
                self.running[claim["id"]] = run
            self._execute(run)
            ran += 1
        return ran

    def drain(self, timeout=10.0):
        """Run jobs until none are due (tests and scripts). Returns how many ran."""
        end = time.monotonic() + timeout
        ran = 0
        while time.monotonic() < end:
            started = self.tick()
            ran += started
            with self.lock:
                busy = bool(self.running)
            if not started and not busy:
                return ran
            time.sleep(0.01)
        raise TimeoutError("jobs still running")


# ------------------------------------------------------------------ schedules

def run_schedules(backend=None, *, now=None, started_at=None):
    """Queue every schedule slot that is due. Safe to call from many servers at once: each slot's
    job has a unique key, so exactly one of them queues it."""
    backend = backend or core.backend()
    now = time.time() if now is None else now
    now_dt = dt.datetime.fromtimestamp(now, UTC)
    queued = 0
    for name, (sched, job_name, catchup) in list(core.SCHEDULES.items()):
        definition = core.REGISTRY.get(job_name)
        if definition is None:
            continue
        try:
            mark = backend.mark(name)
            slots = _due(sched, catchup, mark, now_dt, started_at if started_at is not None else now)
            for slot in slots:
                ts = slot.timestamp()
                backend.enqueue(name=job_name, queue=definition.queue, payload=json.dumps(
                    {"args": [], "kwargs": {}}), run_at=ts, max_attempts=definition.retries + 1,
                    timeout=definition.timeout, unique_key=f"cron:{name}:{int(ts)}",
                    unique_until=ts + CRON_KEY_DAYS * 86400)
                queued += 1
            if slots:
                backend.set_mark(name, slots[-1].timestamp())
        except Exception:  # noqa: BLE001 - one bad schedule mustn't stop the others
            log.exception("schedule %s failed", name)
    return queued


def next_due(now):
    """When the next schedule slot falls (so short intervals aren't late)."""
    now_dt = dt.datetime.fromtimestamp(now, UTC)
    soonest = float("inf")
    for sched, _job, _catchup in list(core.SCHEDULES.values()):
        try:
            soonest = min(soonest, sched.next_after(now_dt).timestamp())
        except Exception:  # noqa: BLE001
            pass
    return soonest


def _due(sched, catchup, mark, now_dt, started_at):
    start_dt = dt.datetime.fromtimestamp(started_at, UTC)
    if mark is None or catchup == "none":
        # A new schedule (or no catching up): only slots from now on, but always the
        # current slot if the server started within it (restarts don't skip a run).
        base = max(start_dt - dt.timedelta(seconds=1), dt.datetime.fromtimestamp(mark, UTC)) if mark else \
            start_dt - dt.timedelta(seconds=1)
        return sched.slots(base, now_dt, limit=100)
    after = dt.datetime.fromtimestamp(mark, UTC)
    if catchup == "all":
        return sched.slots(after, now_dt, limit=100)
    after = max(after, now_dt - dt.timedelta(days=31))       # "latest": only the most recent missed slot
    last = []
    for _ in range(1000):
        batch = sched.slots(after, now_dt, limit=500)
        if not batch:
            break
        last = batch[-1:]
        if len(batch) < 500:
            break
        after = batch[-1]
    return last


# ------------------------------------------------------------------ embedded

def heartbeat_path():
    """Where ``pyweb worker`` records that it's healthy (``PYWEB_WORKER_HEARTBEAT`` overrides)."""
    import tempfile
    return os.environ.get("PYWEB_WORKER_HEARTBEAT") or os.path.join(tempfile.gettempdir(), "pyweb-worker.alive")


def touch(path):
    try:
        with open(path, "a"):
            pass
        os.utime(path, None)
    except OSError:
        pass


def alive(path=None, max_age=90.0):
    """True when a worker finished a healthy round in the last ``max_age`` seconds.

    A worker whose job store is unreachable stops touching the file (it backs
    off up to 60 s between attempts), so it turns unhealthy, and a hung one does too.
    """
    try:
        return time.time() - os.path.getmtime(path or heartbeat_path()) <= max_age
    except OSError:
        return False


def worker_enabled():
    return os.environ.get("PYWEB_WORKER", "1").strip().lower() not in ("0", "false", "no", "off")


def start_embedded(*, concurrency=None):
    """Start a worker in this server process (``pyweb dev`` / ``pyweb serve``), unless ``PYWEB_WORKER=0``."""
    if not worker_enabled():
        return None
    for w in list(WORKERS):
        if w.thread is not None and not w.stopping.is_set():
            return w                                           # already running here
    conc = concurrency or int(os.environ.get("PYWEB_WORKER_CONCURRENCY", "4") or 4)
    return Worker(concurrency=conc).start()


def stop_all(timeout=10.0):
    """Stop every worker in this process; returns how many jobs were handed back unfinished."""
    return sum(w.stop(timeout) for w in list(WORKERS) if w.thread is not None and not w.stopping.is_set())


def local_worker_running():
    return any(w.thread is not None and not w.stopping.is_set() for w in list(WORKERS))
