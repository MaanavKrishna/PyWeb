"""Observability: structured logs, traces, metrics, error mapping.

One logical interaction (button click -> RPC -> DB -> DOM update) shares
a single trace id end to end: the browser runtime sends
``X-Request-Id``/``traceparent`` headers (see :mod:`pyweb.rpc`), the
server joins them via :func:`context_from_headers`, and every span,
log record and error report carries the trace.
"""

from __future__ import annotations

import contextvars
import json
import sys
import threading
import time
import traceback
import uuid


class Logger:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self._lock = threading.Lock()

    def log(self, level, message, **fields):
        rec = {"ts": time.time(), "level": level, "msg": message, **fields}
        with self._lock:
            self.stream.write(json.dumps(rec) + "\n")
            self.stream.flush()

    def info(self, message, **fields):
        self.log("info", message, **fields)

    def error(self, message, **fields):
        self.log("error", message, **fields)


class Tracer:
    def __init__(self):
        self.spans: list[dict] = []
        self._local = threading.local()

    def start(self, name, trace_id=None, **fields):
        trace_id = trace_id or _current_trace.get() or uuid.uuid4().hex[:16]
        span = {"trace": trace_id, "span": uuid.uuid4().hex[:16],
                "name": name, "start": time.time(), "end": None, **fields}
        self.spans.append(span)
        self._local.current = span
        return span

    def finish(self, span):
        span["end"] = time.time()
        span["dur_ms"] = round((span["end"] - span["start"]) * 1000, 3)

    def span(self, name, **fields):
        """``with tracer.span("db.query", table="users"):`` ..."""
        return Span(self, name, **fields)

    def trace(self, name, trace_id=None):
        if trace_id is not None:
            _current_trace.set(trace_id)
        return self.span(name)


class Metrics:
    def __init__(self):
        self.counters: dict[str, int] = {}
        self.timings: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def inc(self, name, amount=1):
        with self._lock:
            self.counters[name] = self.counters.get(name, 0) + amount

    def observe(self, name, value):
        with self._lock:
            self.timings.setdefault(name, []).append(value)

    def summary(self):
        with self._lock:
            out = {"counters": dict(self.counters)}
            for k, vals in self.timings.items():
                out[k] = {"n": len(vals), "avg": sum(vals) / len(vals) if vals else 0,
                          "max": max(vals) if vals else 0}
            return out


def format_error(exc, source_lines=None, filename="<pyweb>"):
    """Map an exception to a PyWeb-style error with source context."""
    tb = traceback.extract_tb(exc.__traceback__)
    frame = tb[-1] if tb else None
    out = [f"{type(exc).__name__}: {exc}"]
    if frame is not None:
        out.append(f"{frame.filename}:{frame.lineno}")
        if source_lines and 0 < frame.lineno <= len(source_lines):
            i = frame.lineno
            start = max(0, i - 3)
            for n, line in enumerate(source_lines[start:i + 1], start=start + 1):
                mark = ">>" if n == i else "  "
                out.append(f"{mark} {n:4d} | {line}")
    return "\n".join(out)


class Timer:
    def __enter__(self):
        self._start = time.time()
        self.elapsed_s = 0.0
        return self

    def __exit__(self, *exc):
        self.elapsed_s = time.time() - self._start
        return False


# ---------------------------------------------------------------------------
# Distributed trace context (W3C traceparent compatible)
# ---------------------------------------------------------------------------

_current_trace: contextvars.ContextVar = contextvars.ContextVar(
    "pyweb_trace", default=None)
_current_span: contextvars.ContextVar = contextvars.ContextVar(
    "pyweb_span", default=None)


def new_trace() -> str:
    """Start a fresh 16-hex-char trace id for an inbound request."""
    trace_id = uuid.uuid4().hex[:16]
    _current_trace.set(trace_id)
    return trace_id


def context_from_headers(headers: dict) -> str:
    """Join an incoming trace or start a new one.

    Accepts ``traceparent`` (``00-<trace>-<span>-<flags>``) and
    ``X-Request-Id``; falls back to :func:`new_trace`. Returns the
    active trace id. Why both: browsers that cannot set custom headers
    (plain form posts, SSE) still send X-Request-Id via query or the
    runtime's fetch wrapper.
    """
    headers = {str(k).lower(): v for k, v in (headers or {}).items()}
    tp = headers.get("traceparent", "")
    parts = tp.split("-")
    if len(parts) == 4 and len(parts[1]) == 32:
        trace_id = parts[1][:16]
    else:
        trace_id = str(headers.get("x-request-id", "")) or ""
    if not trace_id:
        return new_trace()
    _current_trace.set(trace_id[:16])
    return _current_trace.get()


def active_trace() -> str | None:
    return _current_trace.get()


def inject_headers(headers: dict | None = None) -> dict:
    """Outgoing headers carrying the active trace (client/RPC use)."""
    headers = dict(headers or {})
    trace_id = _current_trace.get()
    if trace_id:
        headers.setdefault("X-Request-Id", trace_id)
        span = _current_span.get() or uuid.uuid4().hex[:16]
        headers.setdefault("traceparent", f"00-{trace_id}-{(span)[:16]}-01")
    return headers


class Span:
    """Trace span usable as ``with tracer.span("db.query"):``."""

    def __init__(self, tracer, name, **fields):
        self._tracer = tracer
        self._name = name
        self._fields = fields
        self.span = None

    def __enter__(self):
        trace_id = _current_trace.get() or new_trace()
        self.span = self._tracer.start(
            self._name, trace_id=trace_id, **self._fields)
        _current_span.set(self.span.get("span", ""))
        return self.span

    def __exit__(self, *exc):
        self._tracer.finish(self.span)
        _current_span.set(None)
        return False


# ---------------------------------------------------------------------------
# Error taxonomy: every failure maps to a stable code + safe message.
# ---------------------------------------------------------------------------

_ERROR_CODES = [
    (ValueError, "bad-request"),
    (KeyError, "not-found"),
    (PermissionError, "forbidden"),
]


def register_error_code(exc_type, code: str):
    """Map an exception class to a stable wire code (checked most specific first)."""
    _ERROR_CODES.insert(0, (exc_type, code))


def error_code(exc: BaseException) -> str:
    for exc_type, code in _ERROR_CODES:
        if isinstance(exc, exc_type):
            return code
    return "internal"


def error_report(exc: BaseException, *, source_lines=None,
                 filename="<pyweb>", safe_detail=True) -> dict:
    """Structured error payload: code, trace, source context.

    ``message`` is safe to send to browsers; the original ``repr`` stays
    server-side in logs. ``safe_detail=False`` is for CLI/dev only.
    """
    text = format_error(exc, source_lines, filename)
    lines = text.splitlines()
    return {"code": error_code(exc),
            "message": lines[0] if lines else type(exc).__name__,
            "detail": text if not safe_detail else None,
            "trace": active_trace(),
            "type": type(exc).__name__}


# Register framework error codes (import-light: resolved lazily).
def _register_framework_codes():
    try:
        from pyweb.rpc import RPCError
        register_error_code(RPCError, "rpc")
    except ImportError:
        pass
    try:
        from pyweb import auth as _auth
        register_error_code(_auth.AuthError, "unauthenticated")
        register_error_code(_auth.Forbidden, "forbidden")
        register_error_code(_auth.NotAuthenticated, "unauthenticated")
    except ImportError:
        pass


_register_framework_codes()


# ---------------------------------------------------------------------------
# Time-travel event log: deterministic record/replay of state changes.
# ---------------------------------------------------------------------------

class EventLog:
    """Append-only ring buffer of state/RPC/DB events for debugging.

    ``record("signal", name="count", old=2, new=3)`` captures what the
    DevTools timeline shows. ``replay()`` re-applies signal events to a
    namespace dict so a developer can step through what happened.
    Bounded (default 10k events) so it is safe to leave on in staging.
    """

    def __init__(self, capacity=10_000):
        self._capacity = capacity
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self._seq = 0

    def record(self, kind, **fields):
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "ts": time.time(), "kind": kind,
                     "trace": _current_trace.get(), **fields}
            self._events.append(event)
            del self._events[:-self._capacity]
            return event

    def since(self, seq=0, kind=None, limit=1000):
        with self._lock:
            events = [e for e in self._events if e["seq"] > seq]
        if kind is not None:
            events = [e for e in events if e["kind"] == kind]
        return events[:limit]

    def replay(self, namespace: dict, *, up_to=None):
        """Re-apply ``signal`` events to ``namespace``; returns final seq."""
        last = 0
        for event in self.since(0):
            if up_to is not None and event["seq"] > up_to:
                break
            if event["kind"] == "signal" and "name" in event:
                namespace[event["name"]] = event.get("new")
            last = event["seq"]
        return last

    def to_json(self):
        with self._lock:
            return json.dumps(self._events)


_default_log = EventLog()


def record(kind, **fields):
    return _default_log.record(kind, **fields)
