"""Shared pieces for PyWeb's Redis users (live updates, rate limits, cache, jobs).

* :func:`client` opens a client with short socket timeouts, so a Redis that stops answering
  can't hang a request thread.
* :class:`Breaker` is how each of them degrades: after an error it works without Redis for a few
  seconds, then tries again (it used to give up on Redis for the life of the process).
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("pyweb.redis")

TIMEOUT = 2.0          # seconds for connecting and for each command
RETRY_AFTER = 5.0      # seconds without Redis after an error


def client(url):
    try:
        import redis
    except ImportError as e:
        raise RuntimeError("this needs the 'redis' package: pip install redis") from e
    return redis.Redis.from_url(url, socket_timeout=TIMEOUT, socket_connect_timeout=TIMEOUT,
                                health_check_interval=30)


class Breaker:
    """``ok()`` says whether to use Redis now; ``failed(exc)`` opens the breaker for a few seconds."""

    def __init__(self, what, retry_after=RETRY_AFTER):
        self.what = what
        self.retry_after = retry_after
        self.error = None
        self._until = 0.0
        self._lock = threading.Lock()

    def ok(self):
        return self.error is None or time.monotonic() >= self._until

    def failed(self, exc):
        with self._lock:
            first = self.error is None
            self.error = exc
            self._until = time.monotonic() + self.retry_after
        if first:
            log.warning("%s: Redis failed (%s); working without it, retrying every %.0fs",
                        self.what, exc, self.retry_after)
        try:
            from pyweb.telemetry.instruments import redis_errors
            redis_errors.inc(what=self.what)
        except Exception:  # noqa: BLE001 - metrics are best-effort
            pass

    def worked(self):
        if self.error is not None:
            with self._lock:
                was, self.error = self.error, None
            if was is not None:
                log.info("%s: Redis is back", self.what)

    def __bool__(self):           # `if bus._broken:` reads as "Redis is unusable right now"
        return not self.ok()
