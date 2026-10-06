"""Background jobs.

Durable jobs (0.5)::

    @app.job(retries=5)
    def send_invoice(order_id: int): ...

    send_invoice.enqueue(42)

    @app.cron("0 3 * * *")
    def nightly(): ...

They're stored in the app's database (or Redis with ``PYWEB_JOBS=redis``),
run by workers (``pyweb worker``, or inside ``pyweb dev`` / ``pyweb serve``),
retried with backoff, and joined to the request's transaction. See
docs/24-jobs.md.

``@task`` and ``Queue`` / ``RedisQueue`` (0.4, in-memory) still work.
"""

from .core import (REGISTRY, SCHEDULES, JobDef, JobError, JobHandle, RunContext, backend, backoff_delay, cron,
                   current, durable, every, job, progress, retry, use_backend)
from .legacy import Job, Queue, RedisQueue, _default_queue, task
from .worker import Worker, run_schedules, start_embedded, stop_all
from . import builtin  # noqa: F401,E402 - registers PyWeb's own jobs

__all__ = ["job", "cron", "every", "current", "progress", "retry", "backend", "use_backend", "durable",
           "Worker", "run_schedules", "start_embedded", "stop_all", "JobDef", "JobHandle", "JobError",
           "RunContext", "REGISTRY", "SCHEDULES", "backoff_delay",
           "task", "Queue", "RedisQueue", "Job", "_default_queue"]
