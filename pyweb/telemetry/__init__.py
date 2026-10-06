"""Telemetry: what your app is doing, for you and for your monitoring.

- **Logs** (:mod:`.logs`): JSON lines in production, readable while
  developing; every line carries the request id, trace id, route and user;
  secrets are redacted.
- **Metrics** (:mod:`.metrics`, :mod:`.instruments`): Prometheus text at
  ``/metrics`` (``PYWEB_METRICS_TOKEN``), added up across server processes.
- **Traces** (:mod:`.tracing`): W3C ``traceparent`` in and out, carried into
  background jobs, and OpenTelemetry spans when OTel is configured.
- **Errors**: ``@app.on_error`` hooks get every unhandled error with its
  request context (send them to Sentry, a chat channel, ...).
- **The dev toolbar** (:mod:`.devtools`): each request's queries, N+1
  warnings, spans, jobs and emails while developing. Never in production.
"""

from __future__ import annotations

import collections
import hmac
import logging
import os
import threading

from . import instruments, logs, metrics, tracing
from .tracing import current, note, span

__all__ = ["current", "span", "note", "on_error", "report_error", "metrics_response", "logs", "metrics",
           "tracing", "instruments"]

log = logging.getLogger("pyweb.errors")
access = logging.getLogger("pyweb.access")

ERROR_HOOKS: list = []
_installed = False
_lock = threading.Lock()

#: The last requests handled while developing (the dev toolbar reads these).
RECENT: collections.deque = collections.deque(maxlen=60)
_QUIET_ROUTES = {"health", "static", "metrics", "live", "dev"}
_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}


def install():
    """Hook telemetry into the database layer (once per process)."""
    global _installed
    with _lock:
        if _installed:
            return
        from pyweb.db import QUERY_HOOKS
        if tracing.record_query not in QUERY_HOOKS:
            QUERY_HOOKS.append(tracing.record_query)
        _installed = True


# ------------------------------------------------------------------ errors

def on_error(fn):
    """``@on_error`` (or ``@app.on_error``): ``fn(error, info)`` for every unhandled error.

    ``info`` has ``where`` (``rpc``, ``page``, ``job``, ...), ``request_id``,
    ``trace_id``, ``route``, ``user`` and, for jobs, ``job`` and ``attempt``.
    A hook that raises is logged and skipped; it never breaks the request.
    """
    ident = (getattr(fn, "__module__", None), getattr(fn, "__qualname__", None))
    # The same hook defined again (the dev server reloaded the app) replaces the old one.
    ERROR_HOOKS[:] = [h for h in ERROR_HOOKS if h is not fn and (ident[1] is None or ident != (
        getattr(h, "__module__", None), getattr(h, "__qualname__", None)))]
    ERROR_HOOKS.append(fn)
    return fn


def report_error(exc, where, **info):
    """Count, log and hand an unhandled error to the ``on_error`` hooks (once per error)."""
    if getattr(exc, "__pyweb_reported__", False):
        return
    try:
        exc.__pyweb_reported__ = True
    except (AttributeError, TypeError):
        pass
    instruments.errors.inc(where=where)
    req = current()
    data = {"where": where, **info}
    if req is not None:
        data.setdefault("request_id", req.request_id)
        data.setdefault("trace_id", req.trace_id)
        if req.route:
            data.setdefault("route", req.route)
        if req.user is not None:
            data.setdefault("user", req.user)
        if req.job:
            data.setdefault("job", req.job)
        req.error = f"{type(exc).__name__}: {exc}"
    log.error("%s failed: %s: %s", where, type(exc).__name__, exc,
              exc_info=(type(exc), exc, exc.__traceback__),
              extra={k: v for k, v in info.items() if k not in logs._STD and k != "where"})
    for hook in list(ERROR_HOOKS):
        try:
            hook(exc, dict(data))
        except Exception:  # noqa: BLE001 - an error reporter must not cause errors
            log.exception("on_error hook %s failed", getattr(hook, "__name__", hook))


# --------------------------------------------------------------- requests

def begin_request(method, path, headers, *, trust_proxy=False, dev=False):
    instruments.http_in_flight.inc()
    return tracing.begin("http", headers=headers, trust_proxy=trust_proxy, method=method,
                         path=path.split("?")[0], dev=dev)


def end_request(req, token, status, error=None):
    """Metrics, the access log line and (while developing) the toolbar record for one request."""
    instruments.http_in_flight.dec()
    try:
        route = req.route or "other"
        method = req.method if req.method in _METHODS else "OTHER"
        seconds = req.elapsed_ms / 1000.0
        instruments.http_requests.inc(method=method, route=route, status=str(status))
        instruments.http_seconds.observe(seconds, route=route)
        if req.error is None and isinstance(status, int) and status >= 500:
            req.error = f"HTTP {status}"
        _access_log(req, route, status)
        if req.query_log is not None and route not in _QUIET_ROUTES:
            RECENT.append(_summary(req, status))
    finally:
        tracing.end(token, req, status=status, error=error)


def _access_log(req, route, status):
    if os.environ.get("PYWEB_ACCESS_LOG", "1").strip().lower() in ("0", "false", "off"):
        return
    level = logging.DEBUG if route in _QUIET_ROUTES else (logging.WARNING if req.warnings else logging.INFO)
    if not access.isEnabledFor(level):
        return
    extra = {"method": req.method, "path": req.path, "status": status, "ms": round(req.elapsed_ms, 1)}
    if req.queries:
        extra["queries"] = req.queries
        extra["query_ms"] = round(req.query_ms, 1)
    if req.warnings:
        extra["warnings"] = req.warnings[:3]
    access.log(level, "%s %s %s %.1fms%s", req.method, logs.redact_text(req.path), status, req.elapsed_ms,
               f" ({req.queries} queries)" if req.queries else "", extra=extra)


def _summary(req, status):
    return {"id": req.request_id, "method": req.method, "path": req.path, "route": req.route, "status": status,
            "ms": round(req.elapsed_ms, 2), "queries": req.queries, "query_ms": round(req.query_ms, 2),
            "sql": list(req.query_log or ()), "spans": list(req.spans or ()), "warnings": list(req.warnings),
            "events": list(req.events or ()), "error": req.error, "user": req.user, "trace_id": req.trace_id}


# ----------------------------------------------------------------- /metrics

def metrics_allowed(headers, *, dev=False):
    """``/metrics`` is open while developing; in production it needs ``PYWEB_METRICS_TOKEN``."""
    if dev:
        return True
    token = os.environ.get("PYWEB_METRICS_TOKEN", "")
    if not token:
        return False
    auth = next((v for k, v in (headers or {}).items() if k.lower() == "authorization"), "")
    scheme, _, given = auth.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(given.strip().encode(), token.encode())


def metrics_response():
    body = metrics.render(metrics.collect_all()).encode()
    return 200, [("Content-Type", "text/plain; version=0.0.4; charset=utf-8"), ("Cache-Control", "no-store")], body


def serve_metrics(port, host="0.0.0.0"):
    """``/metrics`` on its own port, on a background thread (``pyweb worker --metrics-port``).

    A separate port is private by design (don't publish it); ``PYWEB_METRICS_TOKEN``
    is still required when set.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the http.server API
            open_ = not os.environ.get("PYWEB_METRICS_TOKEN")
            if self.path.split("?")[0] not in ("/metrics", "/") or not (
                    open_ or metrics_allowed(dict(self.headers.items()))):
                self.send_error(404)
                return
            status, headers, body = metrics_response()
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer((host, int(port)), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="pyweb-metrics-http", daemon=True).start()
    return server
