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
import time

from .runtime.server import Request, Server

RUNTIME_PATH = os.path.join(os.path.dirname(__file__), "runtime", "browser", "runtime.js")

SECURITY_HEADERS = [
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "same-origin"),
    ("X-Frame-Options", "SAMEORIGIN"),
]


def _ctype(path):
    if path.endswith(".js") or path.endswith(".mjs"):
        return "text/javascript; charset=utf-8"
    if path.endswith(".map") or path.endswith(".json"):
        return "application/json"
    if path.endswith(".css"):
        return "text/css; charset=utf-8"
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


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
            asset_urls = {name: f"/static/{p['js']}" for name, p in manifest.get("pages", {}).items()
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
        self.memory_js = {f"{n}.js": p["js"] for n, p in self.app.compiled["pages"].items() if p["js"]}

    def reload(self):
        """Re-load the app from source (dev hot reload)."""
        from .app_loader import LoadedApp
        app = LoadedApp(self.app_path)
        self.app = app
        self._index_memory_js()
        self.server = Server(app=app, debug=self.debug, max_body=self.max_body, **self.server_kwargs)

    # ------------------------------------------------------------ static
    def static(self, rel):
        rel = rel.split("?")[0]
        if self.source_mode:
            if rel == "runtime.js":
                with open(RUNTIME_PATH, "rb") as fh:
                    return 200, [("Content-Type", _ctype(rel)), ("Cache-Control", "no-cache")], fh.read()
            if rel in self.memory_js:
                return 200, [("Content-Type", _ctype(rel)), ("Cache-Control", "no-cache")], \
                    self.memory_js[rel].encode()
        for base in self.static_dirs:
            base = os.path.realpath(base)
            target = os.path.realpath(os.path.join(base, rel))
            if target != base and not target.startswith(base + os.sep):
                return 403, [("Content-Type", "text/plain")], b"forbidden"
            if os.path.isfile(target):
                with open(target, "rb") as fh:
                    body = fh.read()
                cache = "no-cache" if self.source_mode else "public, max-age=31536000, immutable"
                return 200, [("Content-Type", _ctype(target)), ("Cache-Control", cache)], body
        return 404, [("Content-Type", "text/plain")], b"not found"

    # ----------------------------------------------------------- request
    def respond(self, method, path, headers, body=b""):
        bare = path.split("?")[0]
        if bare in ("/healthz", "/health", "/readyz"):
            from . import __version__
            payload = {"ok": True, "version": __version__, "uptime_s": round(time.time() - self.started, 3)}
            out = (200, [("Content-Type", "application/json")], json.dumps(payload).encode())
        elif bare.startswith("/static/") and method in ("GET", "HEAD"):
            out = self.static(bare[len("/static/"):])
        elif len(body or b"") > self.max_body:
            out = (413, [("Content-Type", "application/json")],
                   json.dumps({"error": {"code": "http_413", "message": "request body too large"}}).encode())
        else:
            resp = self.server.handle(Request(method, path, headers, body))
            hdrs = []
            for k, v in resp.headers.items():
                for item in (v if isinstance(v, list) else [v]):
                    hdrs.append((k, str(item)))
            raw = resp.body.encode() if isinstance(resp.body, str) else (resp.body or b"")
            out = (resp.status, hdrs, raw)
        if is_stream(out[2]) and method == "HEAD":
            out = (out[0], out[1], b"")
        status, hdrs, raw = out
        names = {k.lower() for k, _ in hdrs}
        for k, v in SECURITY_HEADERS:
            if k.lower() not in names:
                hdrs.append((k, v))
        ctype = next((v for k, v in hdrs if k.lower() == "content-type"), "")
        if ctype.startswith("text/html") and not self.debug and "content-security-policy" not in names:
            from .serve import csp_for
            hdrs.append(("Content-Security-Policy", csp_for(self.csp, raw) if isinstance(raw, bytes) else self.csp))
        if method == "HEAD":
            hdrs.append(("Content-Length", str(len(raw))))
            raw = b""
        return status, hdrs, raw
