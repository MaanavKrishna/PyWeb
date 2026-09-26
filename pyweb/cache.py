"""Cache interface for PyWeb (Track B): memory + Redis-if-available, TTL + tags."""

from __future__ import annotations

import json
import pickle
import threading
import time


class Cache:
    def get(self, key, default=None):
        raise NotImplementedError

    def set(self, key, value, ttl=None, tags=()):
        raise NotImplementedError

    def delete(self, key):
        raise NotImplementedError

    def clear(self):
        raise NotImplementedError

    def invalidate_tag(self, tag) -> int:
        raise NotImplementedError


class MemoryCache(Cache):
    def __init__(self):
        self._store: dict = {}
        self._tags: dict[str, set] = {}
        self._lock = threading.Lock()

    def set(self, key, value, ttl=None, tags=()):
        expires = None if ttl is None else time.monotonic() + ttl
        with self._lock:
            old = self._store.get(key)
            if old is not None:
                for t in old[2]:
                    self._tags.get(t, set()).discard(key)
            tagset = set(tags)
            self._store[key] = (value, expires, tagset)
            for t in tagset:
                self._tags.setdefault(t, set()).add(key)

    def get(self, key, default=None):
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return default
            value, expires, _ = item
            if expires is not None and expires <= time.monotonic():
                del self._store[key]
                return default
            return value

    def delete(self, key):
        with self._lock:
            item = self._store.pop(key, None)
            if item is None:
                return False
            for t in item[2]:
                self._tags.get(t, set()).discard(key)
            return True

    def clear(self):
        with self._lock:
            self._store.clear()
            self._tags.clear()

    def invalidate_tag(self, tag) -> int:
        with self._lock:
            keys = self._tags.pop(tag, set())
            for key in keys:
                self._store.pop(key, None)
            return len(keys)


def _dumps(value) -> bytes:
    try:
        return b"j:" + json.dumps(value).encode("utf-8")
    except (TypeError, ValueError):
        return b"p:" + pickle.dumps(value)


def _loads(raw: bytes):
    if raw.startswith(b"j:"):
        return json.loads(raw[2:].decode("utf-8"))
    if raw.startswith(b"p:"):
        return pickle.loads(raw[2:])  # noqa: S301 - cache roundtrip of own values
    return raw


class RedisCache(Cache):
    def __init__(self, url: str = "redis://localhost:6379/0", client=None, prefix: str = "pyweb:"):
        if client is not None:
            self._r = client
        else:
            try:
                import redis
            except ImportError as e:
                raise RuntimeError(
                    "RedisCache requires the 'redis' package, which is not "
                    "installed. Install it with: pip install redis"
                ) from e
            self._r = redis.Redis.from_url(url)
        self._prefix = prefix

    def _k(self, key) -> str:
        return f"{self._prefix}k:{key}"

    def _t(self, tag) -> str:
        return f"{self._prefix}tag:{tag}"

    def set(self, key, value, ttl=None, tags=()):
        self._r.set(self._k(key), _dumps(value), ex=ttl)
        for t in tags:
            self._r.sadd(self._t(t), self._k(key))

    def get(self, key, default=None):
        raw = self._r.get(self._k(key))
        if raw is None:
            return default
        return _loads(bytes(raw))

    def delete(self, key):
        return bool(self._r.delete(self._k(key)))

    def clear(self):
        self._r.flushdb()

    def invalidate_tag(self, tag) -> int:
        members = [
            m if isinstance(m, bytes) else str(m).encode("utf-8")
            for m in self._r.smembers(self._t(tag))
        ]
        count = 0
        for m in members:
            count += self._r.delete(m.decode("utf-8", "ignore"))
        self._r.delete(self._t(tag))
        return count


def get_cache(backend: str = "memory", **kwargs) -> Cache:
    if backend == "memory":
        return MemoryCache()
    if backend == "redis":
        return RedisCache(**kwargs)
    raise ValueError(f"unknown cache backend: {backend!r}")
