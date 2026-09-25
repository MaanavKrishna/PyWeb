"""Caching: memory backend, TTL, tags, SWR, @cache decorator."""

from __future__ import annotations

import functools
import time


class MemoryCache:
    def __init__(self, time_fn=None):
        self._store: dict[str, tuple] = {}
        self._tags: dict[str, set[str]] = {}
        self._now = time_fn or time.time

    def get(self, key):
        hit = self._store.get(key)
        if not hit:
            return None, False
        value, exp = hit
        if exp is not None and self._now() > exp:
            self.delete(key)
            return None, False
        return value, True

    def set(self, key, value, *, ttl=None, tags=()):
        exp = self._now() + ttl if ttl else None
        self._store[key] = (value, exp)
        for tag in tags:
            self._tags.setdefault(tag, set()).add(key)

    def delete(self, key):
        self._store.pop(key, None)

    def invalidate_tag(self, tag):
        for key in self._tags.pop(tag, ()):
            self._store.pop(key, None)

    def clear(self):
        self._store.clear()
        self._tags.clear()


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
