"""Production RPC protocol: error codes, tracing, retries, rate limits.

Wire format (unchanged from prototype, extended)::

    POST /__pyweb/rpc/<name>
    Headers: Content-Type: application/json
             X-Request-Id (optional, echoed back)
             traceparent (optional W3C trace context, echoed/propagated)
             X-CSRF-Token (required for cookie-authed calls to protected RPCs)
             Authorization: Bearer <session> (or pyweb_session cookie)
    Body: {"args": {...}}

    Success: 200 {"result": ...}
    Stream:  200 application/x-ndjson, one {"chunk": ...} per line
    Error:   <status> {"error": {"code": ..., "message": ...}}

Why this exists: automatic RPC must feel local *without* hiding failure
semantics. Every failure maps to a stable machine-readable ``code`` plus
an HTTP status, so browser code, retries, and observability can react
correctly. Alternatives considered: gRPC/Connect (heavier, worse browser
story), tRPC-style (JS-only). Plain JSON-over-HTTP keeps ``curl``,
proxies, and edge caches working.
"""

from __future__ import annotations

import collections
import random
import threading
import time
import uuid


class Code:
    VALIDATION = "validation_error"      # 422 — bad args
    AUTH = "unauthenticated"             # 401 — no/invalid session
    FORBIDDEN = "forbidden"              # 403 — missing role/permission
    NOT_FOUND = "not_found"              # 404 — unknown rpc
    RATE_LIMIT = "rate_limited"          # 429 — slow down
    TIMEOUT = "timeout"                  # 504 — fn exceeded rpc_timeout
    CONFLICT = "conflict"                # 409 — optimistic-lock mismatch
    CSRF = "csrf_failed"                 # 403 — bad/missing CSRF token
    INTERNAL = "internal"                # 500 — anything else


_STATUS = {
    Code.VALIDATION: 422, Code.AUTH: 401, Code.FORBIDDEN: 403,
    Code.NOT_FOUND: 404, Code.RATE_LIMIT: 429, Code.TIMEOUT: 504,
    Code.CONFLICT: 409, Code.CSRF: 403, Code.INTERNAL: 500,
}


class RPCError(Exception):
    """Raised by server functions to return a typed cross-boundary error."""

    def __init__(self, code: str = Code.INTERNAL, message: str = "",
                 *, status: int | None = None, details: dict | None = None):
        super().__init__(message or code)
        self.code = code
        self.status = status if status is not None else _STATUS.get(code, 500)
        self.details = details or {}

    def to_dict(self):
        return {"code": self.code, "message": str(self),
                "details": self.details}


def status_for(code: str) -> int:
    return _STATUS.get(code, 500)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def parse_traceparent(value: str | None) -> tuple[str, str] | None:
    """Parse W3C ``traceparent`` into (trace_id, parent_span_id)."""
    try:
        parts = (value or "").split("-")
        if len(parts) >= 3 and len(parts[1]) == 32 and len(parts[2]) == 16:
            return parts[1], parts[2]
    except Exception:
        pass
    return None


def make_traceparent(trace_id: str, span_id: str) -> str:
    return f"00-{trace_id}-{span_id}-01"


class RateLimiter:
    """Sliding-window per-key rate limiter, in memory.

    Keys idle for a whole window are dropped, so memory stays proportional
    to the callers seen in the last ``window`` seconds.
    """

    def __init__(self, max_calls: int = 60, window: float = 60.0,
                 time_fn=None):
        self.max_calls = max_calls
        self.window = window
        self._now = time_fn or time.monotonic
        self._hits: dict[str, collections.deque] = {}
        self._lock = threading.Lock()
        self._swept = self._now()

    def allow(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = self._now()
        with self._lock:
            if now - self._swept >= self.window:
                self._sweep(now)
            hits = self._hits.get(key)
            if hits is None:
                hits = self._hits[key] = collections.deque()
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.max_calls:
                retry = int(self.window - (now - hits[0])) + 1
                return False, max(retry, 1)
            hits.append(now)
            return True, 0

    def _sweep(self, now):
        self._swept = now
        for key in [k for k, h in self._hits.items() if not h or now - h[-1] >= self.window]:
            del self._hits[key]


class RedisRateLimiter(RateLimiter):
    """Sliding-window rate limiter in Redis, shared by every server process.

    Same ``allow()`` as :class:`RateLimiter`. Each key is a sorted set of call
    times that expires on its own. If Redis can't be reached, it falls back to
    limiting in this process rather than failing requests.
    """

    def __init__(self, url="redis://localhost:6379/0", client=None, max_calls: int = 60,
                 window: float = 60.0, prefix="pyweb:rl:"):
        super().__init__(max_calls=max_calls, window=window)
        if client is None:
            import redis  # noqa: PLC0415 - optional dependency
            client = redis.Redis.from_url(url)
        self._r, self._prefix = client, prefix
        self._broken = None

    def allow(self, key: str) -> tuple[bool, int]:
        if self._broken is not None:
            return super().allow(key)
        now = time.time()
        k = self._prefix + key
        try:
            pipe = self._r.pipeline()
            pipe.zremrangebyscore(k, 0, now - self.window)
            pipe.zcard(k)
            pipe.zrange(k, 0, 0, withscores=True)
            _, count, oldest = pipe.execute()
            if count >= self.max_calls:
                first = oldest[0][1] if oldest else now
                return False, max(int(self.window - (now - first)) + 1, 1)
            pipe = self._r.pipeline()
            pipe.zadd(k, {f"{now}:{uuid.uuid4().hex[:8]}": now})
            pipe.pexpire(k, int(self.window * 1000) + 1000)
            pipe.execute()
            return True, 0
        except Exception as exc:  # noqa: BLE001 - degrade to per-process limits, never fail the request
            self._broken = exc
            return super().allow(key)


def default_rate_limiter(max_calls=120, window=60.0):
    """The limiter servers use: shared through Redis when ``PYWEB_REDIS_URL`` is set."""
    from .config import settings
    url = settings().redis_url
    if url:
        try:
            return RedisRateLimiter(url, max_calls=max_calls, window=window)
        except ImportError:
            pass
    return RateLimiter(max_calls=max_calls, window=window)


class RetryPolicy:
    """Client retry policy: which statuses are safe to retry + backoff."""

    RETRYABLE = {502, 503, 504}

    def __init__(self, attempts: int = 3, base_delay: float = 0.1,
                 max_delay: float = 2.0, jitter: float = 0.1):
        self.attempts = max(1, attempts)
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter

    def delay(self, attempt: int) -> float:
        d = min(self.base_delay * (2 ** attempt), self.max_delay)
        return d + random.uniform(0, self.jitter)

    def should_retry(self, attempt: int, status: int | None,
                     exc: Exception | None = None) -> bool:
        if attempt + 1 >= self.attempts:
            return False
        if exc is not None:
            return True  # network-level failure: safe to retry idempotent reads
        return status in self.RETRYABLE
