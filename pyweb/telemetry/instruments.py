"""The metrics PyWeb records (names follow Prometheus conventions)."""

from __future__ import annotations

import time

from .metrics import REGISTRY

http_requests = REGISTRY.counter("pyweb_http_requests_total", "HTTP requests answered",
                                 ("method", "route", "status"))
http_seconds = REGISTRY.histogram("pyweb_http_request_duration_seconds", "Time to answer an HTTP request",
                                  ("route",))
http_in_flight = REGISTRY.gauge("pyweb_http_requests_in_flight", "Requests being handled now")
rpc_errors = REGISTRY.counter("pyweb_rpc_errors_total", "Server functions that raised", ("function", "code"))
db_queries = REGISTRY.counter("pyweb_db_queries_total", "Database queries", ("dialect",))
db_seconds = REGISTRY.histogram("pyweb_db_query_duration_seconds", "Time per database query",
                                buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5))
db_repeats = REGISTRY.counter("pyweb_db_repeated_queries_total",
                              "Requests that ran the same query 10+ times (likely N+1)")
jobs_done = REGISTRY.counter("pyweb_jobs_total", "Job runs by outcome", ("job", "outcome"))
job_seconds = REGISTRY.histogram("pyweb_job_duration_seconds", "Time per job run", ("job",),
                                 buckets=(0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 300))
jobs_enqueued = REGISTRY.counter("pyweb_jobs_enqueued_total", "Jobs queued", ("job",))
live_messages = REGISTRY.counter("pyweb_live_messages_total", "Messages sent to browsers over live sockets")
live_bytes = REGISTRY.counter("pyweb_live_bytes_total", "Bytes sent to browsers over live sockets")
mail_sent = REGISTRY.counter("pyweb_mail_total", "Emails by outcome", ("outcome",))
auth_events = REGISTRY.counter("pyweb_auth_events_total", "Sign-in activity", ("kind", "ok"))
errors = REGISTRY.counter("pyweb_errors_total", "Unhandled errors", ("where",))


def _sockets():
    try:
        from pyweb.net.live import OPEN
        return [({}, len(OPEN))]
    except Exception:  # noqa: BLE001
        return []


_jobs_cache = [0.0, []]


def _jobs_waiting():
    # A grouped count over the jobs table: at most once every 15 seconds, however often you scrape.
    if time.monotonic() - _jobs_cache[0] < 15:
        return _jobs_cache[1]
    try:
        from pyweb.jobs import core
        store = core._backend
        out = []
        if store is not None and hasattr(store, "counts"):
            counts = store.counts()
            out = [({"state": state}, counts.get(state, 0)) for state in ("queued", "running", "failed", "dead")]
    except Exception:  # noqa: BLE001
        out = []
    _jobs_cache[:] = [time.monotonic(), out]
    return out


_STARTED = time.time()
live_sockets = REGISTRY.gauge("pyweb_live_sockets", "Open live-update WebSockets", callback=_sockets)
# Every process reads the same store: take one reading, don't add them up.
jobs_by_state = REGISTRY.gauge("pyweb_jobs", "Jobs in the store by state", ("state",), callback=_jobs_waiting,
                               merge="max")
process_start = REGISTRY.gauge("pyweb_process_start_time_seconds", "When the oldest server process started",
                               callback=lambda: [({}, _STARTED)], merge="min")


def _build_info():
    from pyweb import __version__
    return [({"version": __version__}, 1)]


build_info = REGISTRY.gauge("pyweb_build_info", "PyWeb version (always 1)", ("version",), callback=_build_info,
                            merge="max")
