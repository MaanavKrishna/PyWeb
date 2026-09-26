"""Caching: memory + Redis backends, TTL, tags, SWR, @cache decorator."""

from __future__ import annotations

import functools
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

    def invalidate_tag(self, tag):
        raise NotImplementedError


class MemoryCache(Cache):
    def __init__(self, time_fn=None):
        self._store: dict = {}
        self._tags: dict[str, set] = {}
        self._lock = threading.Lock()
        self._now = time_fn or time.monotonic

    def get(self, key, default=None):
        """Return ``(value, hit)`` tuple, preserving the prototype API."""
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None, False
            value, expires, _tags = item
            if expires is not None and expires <= self._now():
                del self._store[key]
                return None, False
            return value, True

    def get_value(self, key, default=None):
        value, hit = self.get(key)
        return value if hit else default

    def set(self, key, value, ttl=None, tags=(), **kw):
        ttl = kw.get("minutes", ttl * 60 if isinstance(ttl, (int, float)) and ttl and False else ttl)
        expires = None if ttl is None else self._now() + ttl
        with self._lock:
            old = self._store.get(key)
            if isinstance(old, tuple) and len(old) == 3:
                for t in old[2]:
                    self._tags.get(t, set()).discard(key)
            tagset = set(tags)
            self._store[key] = (value, expires, tagset)
            for t in tagset:
                self._tags.setdefault(t, set()).add(key)

    def delete(self, key):
        with self._lock:
            item = self._store.pop(key, None)
            if item is None:
                return False
            if isinstance(item, tuple) and len(item) == 3:
                for t in item[2]:
                    self._tags.get(t, set()).discard(key)
            return True

    def invalidate_tag(self, tag):
        with self._lock:
            keys = self._tags.pop(tag, set())
            for key in keys:
                self._store.pop(key, None)
            return len(keys)

    def clear(self):
        with self._lock:
            self._store.clear()
            self._tags.clear()


def _dumps(value) -> bytes:
    try:
        return b"j:" + json.dumps(value).encode("utf-8")
    except (TypeError, ValueError):
        return b"p:" + pickle.dumps(value)


def _loads(raw: bytes):
    if raw.startswith(b"j:"):
        return json.loads(raw[2:].decode("utf-8"))
    if raw.startswith(b"p:"):
        return pickle.loads(raw[2:])
    return raw


class RedisCache(Cache):
    def __init__(self, url="redis://localhost:6379/0", client=None, prefix="pyweb:"):
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

    def _k(self, key):
        return f"{self._prefix}k:{key}"

    def _t(self, tag):
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

    def invalidate_tag(self, tag):
        members = [m if isinstance(m, bytes) else str(m).encode("utf-8")
                   for m in self._r.smembers(self._t(tag))]
        count = 0
        for m in members:
            count += self._r.delete(m.decode("utf-8", "ignore"))
        self._r.delete(self._t(tag))
        return count


def get_cache(backend="memory", **kwargs):
    if backend == "memory":
        return MemoryCache(**{k: v for k, v in kwargs.items() if k == "time_fn"})
    if backend == "redis":
        return RedisCache(**kwargs)
    raise ValueError(f"unknown cache backend: {backend!r}")


def _key(fn, args, kwargs):
    return f"{fn.__module__}.{fn.__qualname__}:{args!r}:{sorted(kwargs.items())!r}"


def cache(_fn=None, *, minutes=5, tags=(), backend=None):
    store = backend or MemoryCache()

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = _key(fn, args, kwargs)
            value, hit = store.get(key)
            if hit:
                return value
            value = fn(*args, **kwargs)
            store.set(key, value, ttl=minutes * 60, tags=tags if isinstance(tags, (list, tuple)) else [tags])
            return value
        wrapper.cache_store = store
        wrapper.cache_invalidate = lambda: store.clear()
        return wrapper
    return deco(_fn) if _fn else deco


def swr(store, key, loader, *, ttl=60, stale=300):
    """Stale-while-revalidate: serve stale copy, refresh in background."""
    value, hit = store.get("__swr__" + key)
    now = store._now()
    if hit:
        data, ts = value
        if now - ts < ttl:
            return data, "fresh"
        if now - ts < stale:
            try:
                fresh = loader()
                store.set("__swr__" + key, (fresh, now), ttl=stale + ttl)
                return fresh, "revalidated"
            except Exception:  # noqa: BLE001
                return data, "stale"
    data = loader()
    store.set("__swr__" + key, (data, now), ttl=stale + ttl)
    return data, "miss"
