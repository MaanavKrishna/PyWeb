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

    def lookup(self, key):
        """``(value, hit)`` on every backend (``MemoryCache.get`` returns this; ``RedisCache.get`` a value)."""
        value = self.get_value(key, _MISS)
        return (None, False) if value is _MISS else (value, True)


class MemoryCache(Cache):
    def __init__(self, time_fn=None):
        self._store: dict = {}
        self._tags: dict[str, set] = {}
        self._lock = threading.Lock()
        self._now = time_fn or time.monotonic
        self._sets = 0

    def _drop(self, key):
        """Remove ``key`` and its tag entries. Hold the lock."""
        item = self._store.pop(key, None)
        if item is not None:
            for t in item[2]:
                keys = self._tags.get(t)
                if keys is not None:
                    keys.discard(key)
                    if not keys:
                        del self._tags[t]

    def _sweep(self):
        """Forget expired entries nobody asked for again. Hold the lock."""
        now = self._now()
        for key in [k for k, (_v, exp, _t) in self._store.items() if exp is not None and exp <= now]:
            self._drop(key)

    def get(self, key, default=None):
        """Return ``(value, hit)`` tuple, preserving the prototype API."""
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None, False
            value, expires, _tags = item
            if expires is not None and expires <= self._now():
                self._drop(key)
                return None, False
            return value, True

    def get_value(self, key, default=None):
        value, hit = self.get(key)
        return value if hit else default

    def set(self, key, value, ttl=None, tags=(), **kw):
        # ``ttl`` is seconds; ``minutes=`` is the human-friendly alias.
        if "minutes" in kw and kw["minutes"] is not None:
            ttl = kw["minutes"] * 60
        expires = None if ttl is None else self._now() + ttl
        with self._lock:
            self._sets += 1
            if self._sets % 256 == 0:
                self._sweep()
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


def _dumps(value, allow_pickle=False) -> bytes:
    try:
        return b"j:" + json.dumps(value).encode("utf-8")
    except (TypeError, ValueError):
        if not allow_pickle:
            raise TypeError(f"RedisCache stores JSON values; {type(value).__name__} isn't one "
                            "(convert it, or pass allow_pickle=True if only you can write to Redis)") from None
        return b"p:" + pickle.dumps(value)


_MISS = object()


def _loads(raw: bytes, allow_pickle=False):
    if raw.startswith(b"j:"):
        return json.loads(raw[2:].decode("utf-8"))
    if raw.startswith(b"p:"):
        # Unpickling runs code chosen by whoever wrote the bytes: only with explicit trust.
        return pickle.loads(raw[2:]) if allow_pickle else _MISS  # noqa: S301
    return raw


class RedisCache(Cache):
    """A cache in Redis, shared by every server process.

    Values are stored as JSON. ``allow_pickle=True`` also stores other Python
    objects with pickle; only use it when nobody else can write to your Redis,
    because reading a pickle can run arbitrary code.
    """

    _now = staticmethod(time.time)          # shared by every process (for swr)

    def __init__(self, url="redis://localhost:6379/0", client=None, prefix="pyweb:", allow_pickle=False):
        self.allow_pickle = allow_pickle
        if client is not None:
            self._r = client
        else:
            from .redisutil import client as _client
            self._r = _client(url)
        self._prefix = prefix

    def _k(self, key):
        return f"{self._prefix}k:{key}"

    def _t(self, tag):
        return f"{self._prefix}tag:{tag}"

    def set(self, key, value, ttl=None, tags=(), **kw):
        if kw.get("minutes") is not None:
            ttl = kw["minutes"] * 60
        ex = None if ttl is None else max(1, int(-(-ttl // 1)))
        self._r.set(self._k(key), _dumps(value, self.allow_pickle), ex=ex)
        for t in tags:
            tk = self._t(t)
            self._r.sadd(tk, self._k(key))
            # A tag's set lives as long as its longest-lived key, so tags of expired keys go away.
            if ex is None:
                self._r.persist(tk)
            else:
                left = int(self._r.ttl(tk))          # -1: no expiry (new, or holds a key without one)
                if 0 <= left < ex or (left == -1 and int(self._r.scard(tk)) == 1):
                    self._r.expire(tk, ex)

    def get(self, key, default=None):
        raw = self._r.get(self._k(key))
        if raw is None:
            return default
        value = _loads(bytes(raw), self.allow_pickle)
        return default if value is _MISS else value

    def get_value(self, key, default=None):
        return self.get(key, default)

    def delete(self, key):
        return bool(self._r.delete(self._k(key)))

    def clear(self):
        """Delete this cache's keys (the ones under its prefix), nothing else in the database."""
        batch = []
        for pattern in (f"{self._prefix}k:*", f"{self._prefix}tag:*"):
            for k in self._r.scan_iter(match=pattern, count=500):
                batch.append(k)
                if len(batch) >= 500:
                    self._r.delete(*batch)
                    batch = []
        if batch:
            self._r.delete(*batch)

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
        own = f"fn:{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = _key(fn, args, kwargs)
            value, hit = store.lookup(key)
            if hit:
                return value
            value = fn(*args, **kwargs)
            store.set(key, value, ttl=minutes * 60,
                      tags=[own, *(tags if isinstance(tags, (list, tuple)) else [tags])])
            return value
        wrapper.cache_store = store
        wrapper.cache_invalidate = lambda: store.invalidate_tag(own)      # this function's entries only
        return wrapper
    return deco(_fn) if _fn else deco


def swr(store, key, loader, *, ttl=60, stale=300):
    """Stale-while-revalidate: serve stale copy, refresh in background."""
    value, hit = store.lookup("__swr__" + key)
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
