"""Slowing down password guessing without locking people out.

After ``free`` failed attempts for a key (an account, or an IP address),
each further attempt must wait twice as long as the last: 1s, 2s, 4s ...
up to ``cap`` seconds. A success clears the account's count. A hard lockout
would let anyone lock a victim out by guessing badly on purpose; a delay
only slows guessing down.

Counts live in memory, or in Redis with ``PYWEB_REDIS_URL`` so every server
shares them.
"""

from __future__ import annotations

import os
import threading
import time


class Throttle:
    def __init__(self, *, free=5, cap=900, window=3600, redis_url=None, client=None, prefix="pyweb:throttle:"):
        self.free = free
        self.cap = cap
        self.window = window
        self.prefix = prefix
        self._lock = threading.Lock()
        self._mem: dict = {}                       # key -> (count, last_failure_time)
        self._redis = client
        url = redis_url if redis_url is not None else os.environ.get("PYWEB_REDIS_URL")
        if self._redis is None and url:
            try:
                import redis
                self._redis = redis.Redis.from_url(url)
            except ImportError:
                self._redis = None

    def _get(self, key):
        if self._redis is not None:
            try:
                raw = self._redis.hmget(self.prefix + key, "n", "t")
                if raw[0] is None:
                    return 0, 0.0
                return int(raw[0]), float(raw[1] or 0)
            except Exception:  # noqa: BLE001 - Redis down: fall back to this process
                pass
        with self._lock:
            n, t = self._mem.get(key, (0, 0.0))
        if t and time.time() - t > self.window:
            return 0, 0.0
        return n, t

    def wait(self, key):
        """Seconds until ``key`` may try again (0 if now)."""
        n, last = self._get(key)
        if n < self.free:
            return 0
        delay = min(self.cap, 2 ** (n - self.free))
        return max(0, int(last + delay - time.time() + 0.999))

    def fail(self, key):
        now = time.time()
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                pipe.hincrby(self.prefix + key, "n", 1)
                pipe.hset(self.prefix + key, "t", now)
                pipe.expire(self.prefix + key, self.window)
                pipe.execute()
                return
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            n, t = self._mem.get(key, (0, 0.0))
            if t and now - t > self.window:
                n = 0
            self._mem[key] = (n + 1, now)
            if len(self._mem) > 100_000:          # don't grow forever under a spray attack
                cutoff = now - self.window
                self._mem = {k: v for k, v in self._mem.items() if v[1] > cutoff}

    def clear(self, key):
        if self._redis is not None:
            try:
                self._redis.delete(self.prefix + key)
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._mem.pop(key, None)
