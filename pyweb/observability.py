"""Observability helpers: timers, spans, and status formatting (Track D).

Stdlib-only so the CLI and benchmark harness can time compiler, server,
database, and debugger work without extra dependencies.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator


def format_ms(seconds: float) -> str:
    """Format a duration in seconds as milliseconds (e.g. ``12.3ms``)."""
    return f"{seconds * 1000:.1f}ms"


@dataclass
class Span:
    """A single timed operation."""

    name: str
    duration_s: float
    attrs: dict[str, Any] = field(default_factory=dict)


class Tracer:
    """Collects spans; used by the CLI and benchmarks to report timings."""

    def __init__(self) -> None:
        self.spans: list[Span] = []

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.spans.append(Span(name, time.perf_counter() - start, dict(attrs)))

    def total_s(self) -> float:
        return sum(s.duration_s for s in self.spans)

    def summary(self) -> str:
        lines = [f"{s.name}: {format_ms(s.duration_s)}" for s in self.spans]
        lines.append(f"total: {format_ms(self.total_s())}")
        return "\n".join(lines)


class Timer:
    """Simple start/stop timer usable as a context manager."""

    def __init__(self) -> None:
        self.elapsed_s = 0.0
        self._start = 0.0

    def start(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def stop(self) -> float:
        self.elapsed_s = time.perf_counter() - self._start
        return self.elapsed_s

    def __enter__(self) -> "Timer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()


@contextmanager
def timed(tracer: Tracer, name: str, **attrs: Any) -> Iterator[None]:
    """Record a span on *tracer* for the wrapped block."""
    with tracer.span(name, **attrs):
        yield


def measure(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> tuple[Any, float]:
    """Call ``fn(*args, **kwargs)`` and return ``(result, seconds)``."""
    start = time.perf_counter()
    return fn(*args, **kwargs), time.perf_counter() - start
