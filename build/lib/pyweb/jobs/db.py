"""Jobs in the app's database (table ``pyweb_jobs``): durable, transactional, no extra service.

* Enqueueing inside a request joins its transaction (outbox): a rollback
  means the job was never queued.
* Workers claim jobs with ``SELECT ... FOR UPDATE SKIP LOCKED`` (Postgres,
  MySQL 8), so many workers never wait on each other or take the same job;
  on SQLite one ``UPDATE`` claims atomically.
* A claim is a lease: a worker that dies stops renewing it, and once it
  expires another worker runs the job again (that run counts as an attempt).
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import time

from pyweb.models import Field, Index, Model

from .memory import FINAL, Backend, new_id

TABLE = "pyweb_jobs"
MARKS = "pyweb_schedules"


class JobRecord(Model):
    """One queued, running or finished job (shown in /admin)."""

    id: str = Field(primary_key=True, max=16)
    name: str = Field(max=200)
    queue: str = Field("default", max=60)
    payload: str = Field("{}", kind="text")
    state: str = Field("queued", max=10, choices=("queued", "running", "done", "failed", "dead"))
    attempts: int = 0
    max_attempts: int = 1
    timeout: float = 300.0
    run_at: float = 0.0
    locked_by: str | None = Field(None, max=80)
    locked_until: float | None = None
    unique_key: str | None = Field(None, max=200, unique=True)
    unique_until: float | None = None
    progress: float = 0.0
    last_error: str | None = Field(None, kind="text")
    result: str | None = Field(None, kind="text")
    created_at: float = 0.0
    finished_at: float | None = None

    class Meta:
        table = TABLE
        indexes = [Index("queue", "state", "run_at"), Index("state", "locked_until")]

    @property
    def when(self):
        return dt.datetime.fromtimestamp(self.run_at, dt.timezone.utc)


class ScheduleMark(Model):
    """The last slot each schedule queued (for catching up after downtime)."""

    name: str = Field(primary_key=True, max=200)
    last_slot: float = 0.0

    class Meta:
        table = MARKS


MODELS = [JobRecord, ScheduleMark]
COLUMNS = ("id", "name", "queue", "payload", "state", "attempts", "max_attempts", "timeout", "run_at",
           "locked_by", "locked_until", "unique_key", "unique_until", "progress", "last_error", "result",
           "created_at", "finished_at")


class DatabaseBackend(Backend):
    def __init__(self, db, *, create=True):
        super().__init__()
        from pyweb.db.dialect import dialect_of
        self.db = db
        self.dialect = dialect_of(db)
        q = self.dialect.quote
        self.t, self.m = q(TABLE), q(MARKS)
        self.cols = ", ".join(q(c) for c in COLUMNS)
        self._create = create
        self._ensure()

    def _ensure(self):
        """Create the job tables if missing, but never inside someone's transaction (a rollback
        would take them away); apps normally get them from migrations or ``pyweb dev``."""
        if not self._create:
            return
        from pyweb import models as M
        from pyweb.db import _scope
        if self.db._current() is not None or _scope.get() is not None:
            return
        M.ensure_tables(self.db, MODELS)
        self._create = False

    # -------------------------------------------------------------- enqueue
    def enqueue(self, *, name, queue, payload, run_at, max_attempts, timeout, unique_key=None, unique_until=None):
        db, t = self.db, self.t
        now = time.time()
        job_id = new_id()
        values = (job_id, name, queue, payload, "queued", 0, max_attempts, timeout, run_at, None, None,
                  unique_key, unique_until, 0.0, None, None, now, None)
        insert = f"INSERT INTO {t} ({self.cols}) VALUES ({', '.join('?' * len(COLUMNS))})"
        if unique_key is None:
            db.execute(insert, values)
        else:
            # A finished job's key stops counting once its window has passed.
            db.execute(f"UPDATE {t} SET unique_key = NULL WHERE unique_key = ? AND state IN ('done', 'dead') "
                       f"AND (unique_until IS NULL OR unique_until < ?)", (unique_key, now))
            existing = self._by_key(unique_key)
            if existing:
                return existing
            try:
                with db.transaction():                 # a savepoint inside a request's transaction
                    db.execute(insert, values)
            except Exception:  # noqa: BLE001 - another process queued the same key just now
                existing = self._by_key(unique_key)
                if existing:
                    return existing
                raise
        db.after_commit(lambda: self._announce(queue))
        return job_id

    def _by_key(self, key):
        row = self.db.execute(f"SELECT id FROM {self.t} WHERE unique_key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _announce(self, queue):
        self.notify(queue)
        try:
            from pyweb.realtime import current_bus
            current_bus().publish("pyweb.jobs", queue)     # wakes workers in other processes (with Redis)
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- claim
    def reclaim(self, now=None):
        """Put jobs whose lease ran out (their worker died) back in the queue. Returns how many.

        Kept apart from claiming so the claim query only ever reads the
        ``(queue, state, run_at)`` index, never every finished job.
        """
        now = time.time() if now is None else now
        res = self.db.execute(f"UPDATE {self.t} SET state = 'queued', locked_by = NULL, locked_until = NULL "
                              f"WHERE state = 'running' AND locked_until < ?", (now,))
        if res.rowcount:
            self.db.after_commit(lambda: self._announce("default"))
        return res.rowcount or 0

    def claim(self, queues, worker, limit, now=None):
        if limit <= 0 or not queues:
            return []
        self._ensure()
        db, t = self.db, self.t
        now = time.time() if now is None else now
        if now >= getattr(self, "_next_reclaim", 0):
            self._next_reclaim = now + 5
            self.reclaim(now)
        token = f"{worker}:{secrets.token_hex(4)}"[:80]
        marks = ", ".join("?" * len(queues))
        due = f"queue IN ({marks}) AND state = 'queued' AND run_at <= ?"
        params = (*queues, now)
        claim = (f"UPDATE {t} SET state = 'running', locked_by = ?, locked_until = ? + timeout + 30, "
                 f"attempts = attempts + 1")
        if self.dialect.name == "sqlite":
            limit_sql = self.dialect.limit_offset(int(limit), None)
            db.execute(f"{claim} WHERE id IN (SELECT id FROM {t} WHERE {due} ORDER BY run_at {limit_sql}) AND {due}",
                       (token, now, *params, *params))
            rows = db.execute(f"SELECT {self.cols} FROM {t} WHERE locked_by = ?", (token,)).fetchall()
        else:
            with db.transaction():
                ids = [r[0] for r in db.execute(
                    f"SELECT id FROM {t} WHERE {due} ORDER BY run_at LIMIT {int(limit)} FOR UPDATE SKIP LOCKED",
                    params).fetchall()]
                if not ids:
                    return []
                db.execute(f"{claim} WHERE id IN ({', '.join('?' * len(ids))})", (token, now, *ids))
                rows = db.execute(f"SELECT {self.cols} FROM {t} WHERE locked_by = ?", (token,)).fetchall()
        out = []
        for row in rows:
            r = dict(zip(COLUMNS, row))
            out.append({"id": r["id"], "name": r["name"], "payload": r["payload"], "attempt": int(r["attempts"]),
                        "max_attempts": int(r["max_attempts"]), "timeout": float(r["timeout"]), "token": token,
                        "queue": r["queue"]})
        return out

    # ------------------------------------------------------------- outcomes
    def complete(self, job_id, token, result=None):
        res = self.db.execute(
            f"UPDATE {self.t} SET state = 'done', locked_by = NULL, locked_until = NULL, finished_at = ?, "
            f"result = ?, progress = 1, unique_key = CASE WHEN unique_until IS NULL THEN NULL ELSE unique_key END "
            f"WHERE id = ? AND locked_by = ? AND state = 'running'",
            (time.time(), json.dumps(result, default=str), job_id, token))
        return res.rowcount == 1

    def fail(self, job_id, token, error, retry_at=None):
        if retry_at is None:
            res = self.db.execute(
                f"UPDATE {self.t} SET state = 'dead', locked_by = NULL, locked_until = NULL, finished_at = ?, "
                f"last_error = ?, unique_key = CASE WHEN unique_until IS NULL THEN NULL ELSE unique_key END "
                f"WHERE id = ? AND locked_by = ? AND state = 'running'",
                (time.time(), error[:4000], job_id, token))
        else:
            res = self.db.execute(
                f"UPDATE {self.t} SET state = 'queued', locked_by = NULL, locked_until = NULL, run_at = ?, "
                f"last_error = ? WHERE id = ? AND locked_by = ? AND state = 'running'",
                (retry_at, error[:4000], job_id, token))
        return res.rowcount == 1

    def extend(self, job_id, token, until):
        res = self.db.execute(f"UPDATE {self.t} SET locked_until = ? WHERE id = ? AND locked_by = ? "
                              f"AND state = 'running'", (until, job_id, token))
        return res.rowcount == 1

    def release(self, job_id, token):
        res = self.db.execute(
            f"UPDATE {self.t} SET state = 'queued', locked_by = NULL, locked_until = NULL, run_at = ?, "
            f"attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END "
            f"WHERE id = ? AND locked_by = ? AND state = 'running'", (time.time(), job_id, token))
        return res.rowcount == 1

    def progress(self, job_id, value):
        self.db.execute(f"UPDATE {self.t} SET progress = ? WHERE id = ?", (value, job_id))

    # ---------------------------------------------------------------- reads
    def _public(self, row):
        r = dict(zip(COLUMNS, row))
        out = {k: r[k] for k in ("id", "name", "queue", "state", "attempts", "max_attempts", "run_at",
                                 "progress", "last_error", "created_at", "finished_at", "unique_key")}
        out["args"] = json.loads(r["payload"]) if r["payload"] else None
        out["result"] = json.loads(r["result"]) if r["result"] else None
        return out

    def get(self, job_id):
        row = self.db.execute(f"SELECT {self.cols} FROM {self.t} WHERE id = ?", (job_id,)).fetchone()
        return self._public(row) if row else None

    def list(self, state=None, limit=50, name=None):
        where, params = [], []
        if state:
            where.append("state = ?")
            params.append(state)
        if name:
            where.append("name = ?")
            params.append(name)
        sql = f"SELECT {self.cols} FROM {self.t}" + (" WHERE " + " AND ".join(where) if where else "")
        sql += f" ORDER BY created_at DESC {self.dialect.limit_offset(int(limit), None)}"
        return [self._public(r) for r in self.db.execute(sql, tuple(params)).fetchall()]

    def retry(self, job_id):
        res = self.db.execute(
            f"UPDATE {self.t} SET state = 'queued', run_at = ?, attempts = 0, finished_at = NULL "
            f"WHERE id = ? AND state IN ('dead', 'failed')", (time.time(), job_id))
        if res.rowcount == 1:
            row = self.get(job_id)
            self.db.after_commit(lambda: self._announce(row["queue"]))
            return True
        return False

    def purge(self, before):
        res = self.db.execute(
            f"DELETE FROM {self.t} WHERE state IN ({', '.join(repr(s) for s in FINAL)}) AND finished_at < ? "
            f"AND (unique_until IS NULL OR unique_until < ?)", (before, time.time()))
        return res.rowcount

    # ------------------------------------------------------------ schedules
    def mark(self, name):
        row = self.db.execute(f"SELECT last_slot FROM {self.m} WHERE name = ?", (name,)).fetchone()
        return float(row[0]) if row else None

    def set_mark(self, name, slot):
        res = self.db.execute(f"UPDATE {self.m} SET last_slot = ? WHERE name = ? AND last_slot < ?",
                              (slot, name, slot))
        if res.rowcount == 0 and self.mark(name) is None:
            try:
                with self.db.transaction():
                    self.db.execute(f"INSERT INTO {self.m} (name, last_slot) VALUES (?, ?)", (name, slot))
            except Exception:  # noqa: BLE001 - another scheduler inserted it first
                self.set_mark(name, slot)
