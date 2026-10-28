"""Caching rendered pages: ``@app.page("/pricing", cache=300)``.

A cached page is rendered once and the HTML is reused for the next visitors, for up to ``cache``
seconds, and sent with ``Cache-Control: public, max-age=...`` so a CDN can keep it too.

Only what's the same for everyone is cached. A visitor with a session cookie always gets a fresh
render, and a render that read the session, set a cookie (a form's CSRF cookie, say) or opened a
live feed isn't stored: those pages are personal. ``vary=`` keeps one copy per value of a
request header or cookie (``"accept-language"``, ``"cookie:theme"``, ``"header:X-Region"``);
the path and query string are always part of the key.

A cached page is dropped as soon as this process writes to a table the page read while it
rendered (PyWeb watches the queries), or by hand with :func:`invalidate`. With several server
processes, set ``PYWEB_REDIS_URL``: the copies live in Redis and every process sees the same
ones (each drops what its own writes change; others' writes show within ``cache`` seconds).

Every page answered ``200`` also gets an ``ETag``; a browser that already has that version gets
``304 Not Modified`` and no body.
"""

from __future__ import annotations

import contextvars
import hashlib
import threading

_reading: contextvars.ContextVar = contextvars.ContextVar("pyweb_page_tables", default=None)
_store = None
_store_lock = threading.Lock()
_hooked = False


def store():
    """Where cached pages live: Redis with ``PYWEB_REDIS_URL``, else this process's memory."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                from .config import settings
                url = settings().redis_url
                if url:
                    from .cache import RedisCache
                    _store = RedisCache(url, prefix="pyweb:page:")
                else:
                    from .cache import MemoryCache
                    _store = MemoryCache()
    return _store


def invalidate(path=None):
    """Drop cached pages: the ones for ``path`` (any query string), or all of them."""
    s = store()
    return s.invalidate_tag("path:" + path if path else "page")


def _hook(db, sql, params, ms, rowcount):
    """Every query: remember the tables a cached render reads; drop pages when a table is written."""
    from . import livedata
    written = livedata.written_table(sql)
    if written is not None:
        try:
            store().invalidate_tag("table:" + written.lower())
        except Exception:  # noqa: BLE001 - a cache problem must not fail the write
            pass
        return
    tables = _reading.get()
    if tables is not None:
        tables.update(livedata.read_tables(sql))


def _install():
    global _hooked
    if not _hooked:
        from .db import QUERY_HOOKS
        with _store_lock:
            if not _hooked:
                QUERY_HOOKS.append(_hook)
                _hooked = True


def key(name, req, vary):
    parts = [name, req.path]
    headers = req.headers
    for v in vary:
        if v == "accept-language":
            parts.append((headers.get("accept-language") or "").split(",")[0].strip().lower())
        elif v.startswith("cookie:"):
            parts.append((req.cookies or {}).get(v[7:], ""))
        elif v.startswith("header:"):
            parts.append(headers.get(v[7:]) or "")
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:32]


def personal(req):
    """A request that must never get (or make) a shared copy."""
    from .auth import SESSION_COOKIE
    return SESSION_COOKIE in (req.cookies or {}) or bool(req.headers.get("authorization"))


def lookup(name, req, vary):
    try:
        value = store().get_value(key(name, req, vary))
    except Exception:  # noqa: BLE001 - render instead
        return None
    return value if isinstance(value, str) else None


def begin():
    """Start recording the tables a render reads; returns the token for :func:`finish`."""
    _install()
    return _reading.set(set())


def finish(token, name, req, vary, html, seconds):
    """Store the render if nothing personal happened in it."""
    tables = _reading.get()
    _reading.reset(token)
    from .context import current
    rc = current()
    if rc._session_loaded or rc.set_cookies or rc.toasts or getattr(rc, "personal", False):
        return False
    tags = ["page", "path:" + req.path.split("?")[0], *("table:" + t.lower() for t in (tables or ()))]
    try:
        store().set(key(name, req, vary), html, ttl=seconds, tags=tags)
    except Exception:  # noqa: BLE001
        return False
    return True


def etag(body):
    raw = body.encode() if isinstance(body, str) else bytes(body)
    return 'W/"' + hashlib.sha256(raw).hexdigest()[:24] + '"'


def matches(header, tag):
    """Does ``If-None-Match`` name ``tag`` (weak comparison, as for GET)?"""
    if not header:
        return False
    if header.strip() == "*":
        return True
    want = tag[2:] if tag.startswith("W/") else tag
    return any((t.strip()[2:] if t.strip().startswith("W/") else t.strip()) == want for t in header.split(","))
