"""Background jobs: @task decorator, in-memory queue, retries, progress."""

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

    def set_progress(self, value):
        self.progress = max(0.0, min(1.0, float(value)))

    def to_dict(self):
        return {"id": self.id, "fn": self.fn_name, "pending": self.pending,
                "failed": self.failed, "error": self.error,
                "result": self.result, "progress": self.progress}


class Queue:
    """In-memory queue (Redis/RabbitMQ/Kafka adapters plug in here)."""

    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    def submit(self, fn, *args, retries=0, **kwargs):
        job = Job(uuid.uuid4().hex[:12], getattr(fn, "__name__", "fn"))
        with self._lock:
            self.jobs[job.id] = job
        t = threading.Thread(target=self._run, args=(job, fn, args, kwargs, retries), daemon=True)
        self._threads.append(t)
        t.start()
        return job

    def _run(self, job, fn, args, kwargs, retries):
        attempt = 0
        while True:
            try:
                kwargs["_job"] = job
                job.result = fn(*args, **kwargs)
                job.pending = False
                return
            except TypeError:
                # fn takes no _job kwarg
                try:
                    kwargs.pop("_job", None)
                    job.result = fn(*args, **kwargs)
                    job.pending = False
                    return
                except Exception as exc:  # noqa: BLE001
                    err = exc
            except Exception as exc:  # noqa: BLE001
                err = exc
            if attempt >= retries:
                job.pending = False
                job.failed = True
                job.error = f"{type(err).__name__}: {err}"
                job.traceback = traceback.format_exc()
                return
            attempt += 1
            time.sleep(0.01 * attempt)

    def get(self, job_id):
        return self.jobs.get(job_id)

    def wait(self, job, timeout=10.0):
        end = time.time() + timeout
        while job.pending and time.time() < end:
            time.sleep(0.005)
        return job


_default_queue = Queue()


def task(_fn=None, *, retries=0, queue=None):
    q = queue or _default_queue

    def deco(fn):
        def wrapper(*args, **kwargs):
            return q.submit(fn, *args, retries=retries, **kwargs)
        wrapper.__pyweb_location__ = "worker"
        wrapper.sync = fn
        wrapper.queue = q
        return wrapper
    return deco(_fn) if _fn else deco
