"""Serving the kit: ``/__pyweb/ui.css``, ``/__pyweb/theme.css`` and the behaviour modules."""

from __future__ import annotations

import gzip
import hashlib
import os

from pyweb.runtime.server import Response

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_cache: dict = {}


def _file(name):
    path = os.path.join(STATIC, name)
    stamp = os.path.getmtime(path)
    hit = _cache.get(path)
    if hit is None or hit[0] != stamp:
        with open(path, "rb") as fh:
            data = fh.read()
        hit = (stamp, data, hashlib.sha256(data).hexdigest()[:10])
        _cache[path] = hit
    return hit[1], hit[2]


def version(name="ui.css"):
    return _file(name)[1]


def head_tags(app, *, kit=True):
    """``<link>``s for the theme (always, so the auth pages follow it) and the kit's stylesheet."""
    from . import theme as T
    th = T.config(app)
    out = f'<link rel="stylesheet" href="/__pyweb/theme.css?v={th.version()}">'
    if kit:
        out += f'<link rel="stylesheet" href="/__pyweb/ui.css?v={version()}">'
    return out


def handle(server, req):
    path = req.path.split("?")[0]
    if req.method not in ("GET", "HEAD"):
        return None
    # A ?v= fingerprint makes the file cacheable forever; without it, browsers check each time.
    cache = "public, max-age=31536000, immutable" if "?v=" in req.path else "no-cache"
    if path == "/__pyweb/theme.css":
        from . import theme as T
        app = getattr(getattr(server, "app", None), "app", None)
        body = T.config(app).css().encode()
        return Response(200, body, {"Content-Type": "text/css; charset=utf-8", "Cache-Control": cache})
    if path == "/__pyweb/ui.css":
        data, _v = _file("ui.css")
        return Response(200, data, {"Content-Type": "text/css; charset=utf-8", "Cache-Control": cache})
    return None


def gzip_size(name="ui.css"):
    return len(gzip.compress(_file(name)[0], 9))
