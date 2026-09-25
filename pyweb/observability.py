"""Observability: structured logs, traces, metrics, error mapping."""

from __future__ import annotations

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

    def start(self, name, trace_id=None):
        trace_id = trace_id or uuid.uuid4().hex[:12]
        span = {"trace": trace_id, "name": name, "start": time.time(), "end": None}
        self.spans.append(span)
        self._local.current = span
        return span

    def finish(self, span):
        span["end"] = time.time()
        span["dur_ms"] = round((span["end"] - span["start"]) * 1000, 3)

    def trace(self, name, trace_id=None):
        tracer = self

        class Ctx:
            def __enter__(self):
                self.span = tracer.start(name, trace_id)
                return self.span

            def __exit__(self, *exc):
                tracer.finish(self.span)
        return Ctx()


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
