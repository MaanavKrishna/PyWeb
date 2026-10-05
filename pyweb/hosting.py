"""Hosting glue shared by ``pyweb dev`` and the ASGI adapter.

A :class:`Site` answers one HTTP request: static assets, ``/healthz``,
pages and RPC (via :class:`pyweb.runtime.server.Server`). It is
transport-agnostic: callers pass method/path/headers/body and get back
``(status, [(header, value)], body)``, where ``body`` is bytes or, for
Server-Sent Events, a :class:`pyweb.realtime.EventStream`.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import time

from .runtime.server import Request, Server

RUNTIME_PATH = os.path.join(os.path.dirname(__file__), "runtime", "browser", "runtime.js")

def security_headers(secure=False):
    """Headers every response carries (the app's own values win).

    ``secure`` (HTTPS, ``PYWEB_COOKIE_SECURE``) adds HSTS so browsers only
    ever use HTTPS for the site. Override ``Permissions-Policy`` from the app
    if it needs the camera, microphone or location.
    """
    out = [
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "same-origin"),
        ("X-Frame-Options", "SAMEORIGIN"),
        ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"),
        ("Cross-Origin-Opener-Policy", "same-origin-allow-popups"),  # OAuth popups keep working
        ("Cross-Origin-Resource-Policy", "same-origin"),
    ]
    if secure:
        out.append(("Strict-Transport-Security", "max-age=31536000; includeSubDomains"))
    return out


def secure_from_env():
    return os.environ.get("PYWEB_COOKIE_SECURE", "").lower() in ("1", "true")


SECURITY_HEADERS = security_headers()


def _ctype(path):
    if path.endswith(".js") or path.endswith(".mjs"):
        return "text/javascript; charset=utf-8"
    if path.endswith(".map") or path.endswith(".json"):
        return "application/json"
    if path.endswith(".css"):
        return "text/css; charset=utf-8"
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


# `name.<12 hex>.js|css|js.map` from `pyweb build --production`, and npm files under
# `vendor/<package>@<version>/`: their URL changes when their content does.
_VERSIONED = re.compile(r"(^|/)[^/]+\.[0-9a-f]{12}\.(js|css|js\.map)$|(^|/)vendor/(@[^/]+/)?[^/@]+@[^/]+/")
IMMUTABLE = "public, max-age=31536000, immutable"


def static_file(static_dirs, rel, *, query="", if_none_match=None, dev=False):
    """``(status, headers, body)`` for a file under one of ``static_dirs``.

    Content-hashed and versioned files (and ``?v=`` URLs) are cached for a
    year. Everything else, such as your own ``static/app.css``, is revalidated
    with an ETag, so a deploy shows up at once and an unchanged file costs a
    304 without a body.
    """
    rel = rel.split("?")[0]
    for base in static_dirs:
        base = os.path.realpath(base)
        target = os.path.realpath(os.path.join(base, rel))
        if target != base and not target.startswith(base + os.sep):
            return 403, [("Content-Type", "text/plain")], b"forbidden"
        try:
            st = os.stat(target)
        except OSError:
            continue
        if not os.path.isfile(target):
            continue
        etag = f'W/"{st.st_size:x}-{st.st_mtime_ns:x}"'
        if dev:
            cache = "no-cache"
        elif _VERSIONED.search(rel) or re.search(r"(^|&)v=", query or ""):
            cache = IMMUTABLE
        else:
            cache = "public, max-age=0, must-revalidate"
        headers = [("Content-Type", _ctype(target)), ("Cache-Control", cache), ("ETag", etag)]
        if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
            return 304, headers, b""
        with open(target, "rb") as fh:
            return 200, headers, fh.read()
    return 404, [("Content-Type", "text/plain")], b"not found"


_COMPRESSIBLE = ("text/", "application/json", "application/javascript", "image/svg+xml", "application/xml")
_gz_cache: dict = {}


def gzip_response(headers, body, accept_encoding):
    """Gzip ``body`` when the client accepts it and it's worth it; returns ``(headers, body)``.

    ``headers`` is a list of ``(name, value)``. Static files (those with an ETag)
    are compressed once and kept, so repeat requests cost a dictionary lookup.
    """
    if not isinstance(body, (bytes, bytearray)) or len(body) < 1024 or "gzip" not in (accept_encoding or ""):
        return headers, body
    names = {k.lower(): v for k, v in headers}
    ctype = names.get("content-type", "")
    if "content-encoding" in names or not ctype.startswith(_COMPRESSIBLE):
        return headers, body
    import gzip
    etag = names.get("etag")
    key = (etag, len(body)) if etag else None
    packed = _gz_cache.get(key) if key else None
    if packed is None:
        packed = gzip.compress(bytes(body), 6, mtime=0)
        if key:
            if len(_gz_cache) > 512:
                _gz_cache.clear()
            _gz_cache[key] = packed
    if len(packed) >= len(body):
        return headers, body
    vary = [v for k, v in headers if k.lower() == "vary"]
    out = [(k, v) for k, v in headers if k.lower() not in ("content-length", "vary")]
    out += [("Content-Encoding", "gzip"), ("Vary", ", ".join(vary + ["Accept-Encoding"]))]
    return out, packed


def is_stream(body):
    return not isinstance(body, (bytes, bytearray, str))


def write_http(handler, status, headers, body, *, head=False):
    """Write one response from a ``http.server`` handler; streams are flushed
    chunk by chunk and end the connection."""
    stream = is_stream(body)
    handler.send_response(status)
    for k, v in headers:
        if k.lower() != "content-length":
            handler.send_header(k, v)
    if stream:
        handler.send_header("Connection", "close")
        handler.close_connection = True
    else:
        handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if head:
        return
    if not stream:
        handler.wfile.write(body)
        return
    try:
        for chunk in body:
            handler.wfile.write(chunk)
            handler.wfile.flush()
    except OSError:  # the client went away
        pass
    finally:
        body.close()


class Site:
    """Serve an app from source (``app.pyweb``) or from a built ``dist/``."""

    def __init__(self, target, *, debug=False, max_body=1_048_576, **server_kwargs):
        from .app_loader import LoadedApp
        from .serve import DEFAULT_CSP, load_dist
        self.debug = debug
        self.max_body = max_body
        self.started = time.time()
        self.csp = os.environ.get("PYWEB_CSP", DEFAULT_CSP)
        self.server_kwargs = dict(server_kwargs)
        # Same production defaults as `pyweb serve`.
        if "rate_limit" not in self.server_kwargs and not debug:
            from .rpc import RateLimiter
            self.server_kwargs["rate_limit"] = RateLimiter(max_calls=120, window=60.0)
        elif self.server_kwargs.get("rate_limit") is False:
            self.server_kwargs["rate_limit"] = None
        self.server_kwargs.setdefault("auth_secret", os.environ.get("PYWEB_AUTH_SECRET"))
        self.server_kwargs.setdefault("csrf_secret", os.environ.get("PYWEB_CSRF_SECRET"))
        self.server_kwargs.setdefault(
            "secure_cookies", os.environ.get("PYWEB_COOKIE_SECURE", "").lower() in ("1", "true"))
        if os.path.isdir(target):
            manifest, static_dir, _ = load_dist(target)
            self.static_dirs = [static_dir]
            self.memory_js = {}
            asset_urls = {name: f"/static/{p['js']}" for name, p in {**manifest.get("pages", {}), **manifest.get("layouts", {})}.items()
                          if p.get("js")}
            self.app = LoadedApp(os.path.join(target, manifest.get("app") or "app.pyweb"),
                                 asset_urls=asset_urls)
            self.source_mode = False
        else:
            self.app_path = target
            self.static_dirs = [os.path.join(os.path.dirname(os.path.abspath(target)), "static")]
            self.source_mode = True
            self.app = LoadedApp(target)
            self._index_memory_js()
        self.server = Server(app=self.app, debug=debug, max_body=max_body, **server_kwargs)

    def _index_memory_js(self):
        units = {**self.app.compiled.get("layouts", {}), **self.app.compiled["pages"]}
        self.memory_js = {f"{n}.js": p["js"] for n, p in units.items() if p["js"]}

    def reload(self):
        """Re-load the app from source (dev hot reload)."""
        from .app_loader import LoadedApp
        app = LoadedApp(self.app_path)
        self.app = app
        self._index_memory_js()
        self.server = Server(app=app, debug=self.debug, max_body=self.max_body, **self.server_kwargs)

    # ------------------------------------------------------------ static
    def static(self, rel, *, query="", if_none_match=None):
        rel, _, q = rel.partition("?")
        query = query or q
        if self.source_mode:
            if rel in ("runtime.js", "markdown.js"):
                with open(os.path.join(os.path.dirname(RUNTIME_PATH), rel), "rb") as fh:
                    return 200, [("Content-Type", _ctype(rel)), ("Cache-Control", "no-cache")], fh.read()
            if rel in self.memory_js:
                return 200, [("Content-Type", _ctype(rel)), ("Cache-Control", "no-cache")], \
                    self.memory_js[rel].encode()
        return static_file(self.static_dirs, rel, query=query, if_none_match=if_none_match, dev=self.source_mode)

    # ----------------------------------------------------------- request
    def respond(self, method, path, headers, body=b"", client=None):
        bare = path.split("?")[0]
        if bare in ("/healthz", "/health", "/readyz"):
            from . import __version__
            payload = {"ok": True, "version": __version__, "uptime_s": round(time.time() - self.started, 3)}
            out = (200, [("Content-Type", "application/json")], json.dumps(payload).encode())
        elif bare.startswith("/static/") and method in ("GET", "HEAD"):
            query = path.partition("?")[2]
            inm = next((v for k, v in (headers or {}).items() if k.lower() == "if-none-match"), None)
            out = self.static(bare[len("/static/"):], query=query, if_none_match=inm)
        elif len(body or b"") > self.max_body:
            out = (413, [("Content-Type", "application/json")],
                   json.dumps({"error": {"code": "http_413", "message": "request body too large"}}).encode())
        else:
            resp = self.server.handle(Request(method, path, headers, body, client=client))
            hdrs = []
            for k, v in resp.headers.items():
                for item in (v if isinstance(v, list) else [v]):
                    hdrs.append((k, str(item)))
            raw = resp.body.encode() if isinstance(resp.body, str) else (resp.body or b"")
            out = (resp.status, hdrs, raw)
        if is_stream(out[2]) and method == "HEAD":
            out[2].close()               # never sent: release it (and its connection slot) now
            out = (out[0], out[1], b"")
        status, hdrs, raw = out
        names = {k.lower() for k, _ in hdrs}
        for k, v in security_headers(self.server_kwargs.get("secure_cookies")):
            if k.lower() not in names:
                hdrs.append((k, v))
        ctype = next((v for k, v in hdrs if k.lower() == "content-type"), "")
        if ctype.startswith("text/html") and not self.debug and "content-security-policy" not in names:
            from .serve import csp_for
            hdrs.append(("Content-Security-Policy", csp_for(self.csp, raw) if isinstance(raw, bytes) else self.csp))
        if not is_stream(raw) and status == 200:
            ae = next((v for k, v in (headers or {}).items() if k.lower() == "accept-encoding"), "")
            hdrs, raw = gzip_response(hdrs, raw, ae)
        if method == "HEAD":
            hdrs.append(("Content-Length", str(len(raw))))
            raw = b""
        return status, hdrs, raw
