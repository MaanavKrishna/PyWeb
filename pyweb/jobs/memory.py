"""Jobs kept in this process (scripts, tests, apps without a database). Lost on restart.

Every backend has the same methods and meaning; this one is the reference.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
import uuid

FINAL = ("done", "dead")


def new_id():
    return uuid.uuid4().hex[:16]


def when_committed(fn):
    """Run ``fn`` once the app database's current transaction commits (now if there is none):
    stores outside the database still only queue jobs whose request succeeded."""
    from pyweb import models as M
    db = M.database()
    if db is not None and getattr(db, "_current", lambda: None)() is not None:
        db.after_commit(fn)
        return False
    fn()
    return True


class Backend:
    """Wake-ups shared by every backend: workers in this process hear about new jobs at once."""

    def __init__(self):
        self.listeners = []

    def notify(self, queue):
        for fn in list(self.listeners):
            try:
                fn(queue)
            except Exception:  # noqa: BLE001
                pass


class MemoryBackend(Backend):
    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()
        self.rows: dict[str, dict] = {}
        self.marks: dict[str, float] = {}
        self._tokens = itertools.count(1)

    def enqueue(self, *, name, queue, payload, run_at, max_attempts, timeout, unique_key=None, unique_until=None):
        job_id = new_id()
        holder = []
        when_committed(lambda: holder.append(self._insert(job_id, name, queue, payload, run_at, max_attempts,
                                                          timeout, unique_key, unique_until)))
        return holder[0] if holder else job_id

    def _insert(self, job_id, name, queue, payload, run_at, max_attempts, timeout, unique_key, unique_until):
        now = time.time()
        with self.lock:
            if unique_key is not None:
                for row in self.rows.values():
                    if row["unique_key"] != unique_key:
                        continue
                    if row["state"] not in FINAL or (row["unique_until"] or 0) > now:
                        return row["id"]
                    row["unique_key"] = None
            self.rows[job_id] = {
                "id": job_id, "name": name, "queue": queue, "payload": payload, "state": "queued",
                "attempts": 0, "max_attempts": max_attempts, "timeout": timeout, "run_at": run_at,
                "locked_by": None, "locked_until": None, "unique_key": unique_key, "unique_until": unique_until,
                "progress": 0.0, "last_error": None, "result": None, "created_at": now, "finished_at": None,
            }
        self.notify(queue)
        return job_id

    def claim(self, queues, worker, limit, now=None):
        now = time.time() if now is None else now
        out = []
        with self.lock:
            ready = sorted((r for r in self.rows.values() if r["queue"] in queues and (
                (r["state"] == "queued" and r["run_at"] <= now) or
                (r["state"] == "running" and (r["locked_until"] or 0) < now))), key=lambda r: r["run_at"])
            for row in ready[:limit]:
                token = f"{worker}:{next(self._tokens)}"
                row.update(state="running", locked_by=token, locked_until=now + row["timeout"] + 30,
                           attempts=row["attempts"] + 1)
                out.append(_claimed(row))
        return out

    def _mine(self, job_id, token):
        row = self.rows.get(job_id)
        return row if row is not None and row["locked_by"] == token and row["state"] == "running" else None

    def complete(self, job_id, token, result=None):
        with self.lock:
            row = self._mine(job_id, token)
            if row is None:
                return False
            row.update(state="done", locked_by=None, locked_until=None, finished_at=time.time(),
                       result=json.dumps(result, default=str), progress=1.0)
            if row["unique_until"] is None:
                row["unique_key"] = None
            return True

    def fail(self, job_id, token, error, retry_at=None):
        with self.lock:
            row = self._mine(job_id, token)
            if row is None:
                return False
            row.update(last_error=error[:4000], locked_by=None, locked_until=None)
            if retry_at is None:
                row.update(state="dead", finished_at=time.time())
                if row["unique_until"] is None:
                    row["unique_key"] = None
            else:
                row.update(state="queued", run_at=retry_at)
            queue = row["queue"]
        self.notify(queue)
        return True

    def extend(self, job_id, token, until):
        with self.lock:
            row = self._mine(job_id, token)
            if row is not None:
                row["locked_until"] = until
            return row is not None

    def release(self, job_id, token):
        """Give a claimed job back (shutting down): it runs again soon, the attempt not counted."""
        with self.lock:
            row = self._mine(job_id, token)
            if row is None:
                return False
            row.update(state="queued", locked_by=None, locked_until=None, run_at=time.time(),
                       attempts=max(0, row["attempts"] - 1))
            return True

    def progress(self, job_id, value):
        with self.lock:
            if job_id in self.rows:
                self.rows[job_id]["progress"] = value

    def get(self, job_id):
        with self.lock:
            row = self.rows.get(job_id)
            return _public(row) if row else None

    def list(self, state=None, limit=50, name=None):
        with self.lock:
            rows = [r for r in self.rows.values() if (state is None or r["state"] == state)
                    and (name is None or r["name"] == name)]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return [_public(r) for r in rows[:limit]]

    def retry(self, job_id):
        with self.lock:
            row = self.rows.get(job_id)
            if row is None or row["state"] not in ("dead", "failed"):
                return False
            row.update(state="queued", run_at=time.time(), attempts=0, finished_at=None)
            queue = row["queue"]
        self.notify(queue)
        return True

    def purge(self, before):
        with self.lock:
            gone = [k for k, r in self.rows.items() if r["state"] in FINAL and (r["finished_at"] or 0) < before
                    and (r["unique_until"] or 0) < time.time()]
            for k in gone:
                del self.rows[k]
            return len(gone)

    def mark(self, name):
        return self.marks.get(name)

    def set_mark(self, name, slot):
        with self.lock:
            if slot > self.marks.get(name, float("-inf")):
                self.marks[name] = slot


def _claimed(row):
    return {"id": row["id"], "name": row["name"], "payload": row["payload"], "attempt": row["attempts"],
            "max_attempts": row["max_attempts"], "timeout": row["timeout"], "token": row["locked_by"],
            "queue": row["queue"]}


def _public(row):
    out = {k: row[k] for k in ("id", "name", "queue", "state", "attempts", "max_attempts", "run_at",
                               "progress", "last_error", "created_at", "finished_at", "unique_key")}
    out["args"] = json.loads(row["payload"]) if row.get("payload") else None
    out["result"] = json.loads(row["result"]) if row.get("result") else None
    return out
