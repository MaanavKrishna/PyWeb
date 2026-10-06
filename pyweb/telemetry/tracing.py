"""Request context and tracing.

Each request (and each job run) gets a :class:`RequestTelemetry`: its
request id, W3C trace id, route, user, and what it did (queries, time).
Logs, metrics, the dev toolbar and error reports read it.

A job queued during a request carries the request's ``traceparent``, so the
job's run is part of the same trace: one trace shows the click, the server
function, its queries, the job it queued and that job's queries.

OpenTelemetry: with ``opentelemetry-api`` installed and a tracer provider
configured (by you, or with ``PYWEB_OTEL=1`` plus ``opentelemetry-sdk`` and
the OTLP exporter, which then reads the standard ``OTEL_*`` variables),
requests, server functions, queries and jobs become OTel spans with the
same ids. Without it, PyWeb keeps its own lightweight spans (shown in the
dev toolbar) and the ids still flow through logs and headers.
"""

from __future__ import annotations

import contextvars
import os
import re
import secrets
import time

_current: contextvars.ContextVar = contextvars.ContextVar("pyweb_telemetry", default=None)
_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
REPEAT_LIMIT = 10           # the same query this many times in one request looks like N+1


class RequestTelemetry:
    __slots__ = ("request_id", "trace_id", "span_id", "parent_span", "sampled", "kind", "route", "method",
                 "path", "user", "job", "start", "status", "queries", "query_ms", "shapes", "query_log",
                 "spans", "warnings", "otel_span", "otel_token", "keep", "events", "error")

    def __init__(self, kind):
        self.kind = kind
        self.request_id = self.trace_id = self.span_id = ""
        self.parent_span = None
        self.sampled = True
        self.route = self.method = self.path = None
        self.user = None
        self.job = None
        self.start = time.perf_counter()
        self.status = None
        self.queries = 0
        self.query_ms = 0.0
        self.shapes: dict = {}
        self.query_log: list | None = None      # filled while developing (dev toolbar)
        self.spans: list | None = None
        self.warnings: list = []
        self.otel_span = None
        self.otel_token = None
        self.keep = False
        self.events: list | None = None         # dev toolbar: jobs queued, emails, live pushes
        self.error = None

    @property
    def elapsed_ms(self):
        return (time.perf_counter() - self.start) * 1000

    @property
    def traceparent(self):
        return f"00-{self.trace_id}-{self.span_id}-{'01' if self.sampled else '00'}"


def current():
    """The telemetry of the request (or job) being handled, or None."""
    return _current.get()


def parse_traceparent(value):
    """``(trace_id, parent_span_id, sampled)`` from a W3C ``traceparent`` header, or None."""
    m = _TRACEPARENT.match((value or "").strip().lower())
    if not m or m.group(1) == "0" * 32 or m.group(2) == "0" * 16:
        return None
    return m.group(1), m.group(2), int(m.group(3), 16) & 1 == 1


def begin(kind="http", *, headers=None, trust_proxy=False, traceparent=None, route=None, method=None,
          path=None, job=None, dev=False):
    """Start a request's (or job's) telemetry; returns it and a token for :func:`end`."""
    req = RequestTelemetry(kind)
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    parsed = parse_traceparent(traceparent or headers.get("traceparent"))
    if parsed:
        req.trace_id, req.parent_span, req.sampled = parsed
    else:
        req.trace_id = secrets.token_hex(16)
    req.span_id = secrets.token_hex(8)
    req.route, req.method, req.path, req.job = route, method, path, job
    if dev:
        req.query_log, req.spans, req.events = [], [], []
    tracer = otel_tracer()
    if tracer is not None:
        try:
            from opentelemetry import context as otel_context
            from opentelemetry import trace
            from opentelemetry.propagate import extract
            parent = extract({"traceparent": headers.get("traceparent") or traceparent}) if parsed else None
            title = f"job {job}" if kind == "job" else f"{method or kind} {path or route or ''}".strip()
            span = tracer.start_span(title, context=parent,
                                     kind=trace.SpanKind.SERVER if kind == "http" else trace.SpanKind.CONSUMER)
            ctx = span.get_span_context()
            req.trace_id, req.span_id = format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
            req.otel_span = span
            req.otel_token = otel_context.attach(trace.set_span_in_context(span))
        except Exception:  # noqa: BLE001 - tracing must never break a request
            req.otel_span = None
    incoming = headers.get("x-request-id", "")
    # One id to search for: the trace id, unless a proxy you run (PYWEB_TRUST_PROXY) already named the request.
    req.request_id = incoming if trust_proxy and _SAFE_ID.match(incoming) else req.trace_id
    return req, _current.set(req)


def note(kind, **data):
    """Record something the request did, for the dev toolbar (a no-op outside development)."""
    req = current()
    if req is not None and req.events is not None and len(req.events) < 200:
        req.events.append({"kind": kind, **data})


def end(token, req, *, status=None, error=None):
    req.status = status
    if req.otel_span is not None:
        try:
            from opentelemetry import context as otel_context
            from opentelemetry.trace import Status, StatusCode
            span = req.otel_span
            if req.kind == "http" and req.route:
                span.update_name(f"{req.method} {req.route}")     # low cardinality: the route, not the path
            for k, v in (("http.route", req.route), ("http.request.method", req.method),
                         ("http.response.status_code", status), ("enduser.id", req.user),
                         ("pyweb.db.queries", req.queries), ("pyweb.request_id", req.request_id)):
                if v is not None:
                    span.set_attribute(k, v)
            if error is not None:
                span.record_exception(error)
                span.set_status(Status(StatusCode.ERROR, str(error)[:200]))
            elif isinstance(status, int) and status >= 500:
                span.set_status(Status(StatusCode.ERROR))
            span.end()
            otel_context.detach(req.otel_token)
        except Exception:  # noqa: BLE001
            pass
    _current.reset(token)


class span:
    """``with span("charge card", amount=10):`` a child span of the current request (and an OTel span)."""

    def __init__(self, name, /, **attrs):
        self.name, self.attrs = name, attrs
        self._otel = None
        self._start = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        tracer = otel_tracer()
        if tracer is not None:
            try:
                self._otel = tracer.start_as_current_span(self.name, attributes={
                    k: v for k, v in self.attrs.items() if isinstance(v, (str, int, float, bool))})
                self._otel.__enter__()
            except Exception:  # noqa: BLE001
                self._otel = None
        return self

    def __exit__(self, *exc):
        ms = (time.perf_counter() - self._start) * 1000
        req = current()
        if req is not None and req.spans is not None and len(req.spans) < 500:
            req.spans.append({"name": self.name, "ms": round(ms, 2),
                              "attrs": {k: str(v)[:100] for k, v in self.attrs.items()}})
        if self._otel is not None:
            try:
                self._otel.__exit__(*exc)
            except Exception:  # noqa: BLE001
                pass
        return False


# ---------------------------------------------------------------- queries

def record_query(db, sql, params, ms, rows):
    """A query finished (pyweb.db's query hook): count it, time it, spot repeats (N+1)."""
    from . import instruments as I
    I.db_queries.inc(dialect=getattr(getattr(db, "dialect", None), "name", "db"))
    I.db_seconds.observe(ms / 1000.0)
    req = current()
    if req is None:
        return
    req.queries += 1
    req.query_ms += ms
    shape = " ".join(sql.split())[:300]
    n = req.shapes.get(shape, 0) + 1
    req.shapes[shape] = n
    if n == REPEAT_LIMIT:
        I.db_repeats.inc()
        req.warnings.append(f"the same query ran {REPEAT_LIMIT}+ times in one request (N+1?): {shape[:160]}")
    if req.query_log is not None and len(req.query_log) < 300:
        from .logs import redact
        req.query_log.append({"sql": shape, "ms": round(ms, 2), "rows": rows,
                              "params": redact([str(p)[:60] for p in (params or ())][:10])})
    tracer = otel_tracer()
    if tracer is not None:
        try:
            end_ns = time.time_ns()
            s = tracer.start_span("db " + (sql.split(None, 1)[0].upper() if sql.strip() else "query"),
                                  start_time=end_ns - int(ms * 1e6),
                                  attributes={"db.system": getattr(getattr(db, "dialect", None), "name", "db"),
                                              "db.statement": shape})
            s.end(end_time=end_ns)
        except Exception:  # noqa: BLE001
            pass


# -------------------------------------------------------------- OTel bridge

_tracer = None
_checked = False


def otel_tracer():
    """The OpenTelemetry tracer, when OTel is installed and a provider is configured; else None."""
    global _tracer, _checked
    if _checked:
        return _tracer
    _checked = True
    if os.environ.get("PYWEB_OTEL", "").lower() in ("0", "false", "off"):
        return None
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    if os.environ.get("PYWEB_OTEL", "").lower() in ("1", "true", "on"):
        _configure_sdk()
    provider = trace.get_tracer_provider()
    if type(provider).__name__ in ("ProxyTracerProvider", "NoOpTracerProvider"):
        return None                     # nobody set up OTel: don't pay for spans nobody receives
    from pyweb import __version__
    _tracer = trace.get_tracer("pyweb", __version__)
    return _tracer


def _configure_sdk():
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return
    if type(trace.get_tracer_provider()).__name__ not in ("ProxyTracerProvider", "NoOpTracerProvider"):
        return
    provider = TracerProvider(resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "pyweb")}))
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    except ImportError:
        pass
    trace.set_tracer_provider(provider)


def reset_otel():
    """Forget the cached tracer (tests, or after configuring OTel late)."""
    global _tracer, _checked
    _tracer, _checked = None, False
