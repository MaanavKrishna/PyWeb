"""Production server: threaded stdlib HTTP over a built ``dist/``.

Unlike ``pyweb dev`` (recompiles source, ``exec()``s page code), ``serve``
never executes app source. It loads the build manifest, serves hashed
static assets with immutable caching, answers ``/healthz`` for
orchestrators, and routes pages + RPC through the compiled server
runtime. ``--app mod:attr`` optionally mounts live RPC implementations
from an importable factory returning ``(compiled, rpc_impls)``.
"""

from __future__ import annotations

import http.server
import json
import os
import socketserver
import threading
import time

VERSION = "1.0.0"


class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def load_dist(dist):
    manifest_path = os.path.join(dist, "manifest.json")
    with open(manifest_path) as fh:
        manifest = json.load(fh)
    static_dir = os.path.join(dist, "static")
    server_dir = os.path.join(dist, "server")
    return manifest, static_dir, server_dir


def make_handler(*, static_dir, server_runtime=None, logger=None,
                 started_at=None):
    started = started_at or time.time()

    class H(http.server.BaseHTTPRequestHandler):
        server_version = f"PyWeb/{VERSION}"

        def _bytes(self, body, status=200, ctype="text/plain", extra=None):
            raw = body.encode() if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def _static(self, rel):
            # Contain path traversal: rel must resolve inside static_dir.
            base = os.path.realpath(static_dir)
            target = os.path.realpath(os.path.join(base, rel))
            if target != base and not target.startswith(base + os.sep):
                return self._bytes("forbidden", 403)
            if not os.path.isfile(target):
                return self._bytes("not found", 404)
            ctype = "text/javascript" if target.endswith(".js") else (
                "text/css" if target.endswith(".css") else
                "application/json" if target.endswith(".map") else
                "application/octet-stream")
            extra = {}
            if "?" in self.path or target.endswith((".js", ".css", ".map")):
                # Production assets are content-hashed; dev ``?v=`` query
                # strings version the rest. Both are safe to cache forever.
                extra["Cache-Control"] = "public, max-age=31536000, immutable"
            with open(target, "rb") as fh:
                return self._bytes(fh.read(), 200, ctype, extra)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            if path in ("/healthz", "/health", "/readyz"):
                uptime = round(time.time() - started, 3)
                return self._bytes(
                    json.dumps({"ok": True, "version": VERSION,
                                "uptime_s": uptime}),
                    200, "application/json")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):].split("?")[0])
            if server_runtime is not None:
                from pyweb.runtime.server import Request
                resp = server_runtime.handle(Request("GET", path, dict(self.headers)))
                return self._bytes(resp.body, resp.status,
                                   resp.headers.get("Content-Type", "text/html"),
                                   {k: v for k, v in resp.headers.items()
                                    if k != "Content-Type"})
            # Static fallback: pre-rendered page shell if present.
            return self._bytes("not found", 404)

        def do_POST(self):  # noqa: N802
            if server_runtime is None:
                return self._bytes(json.dumps(
                    {"ok": False, "error": "rpc-unavailable",
                     "message": "serve has no --app factory; "
                                "run with live RPC impls or use pyweb dev"}),
                    501, "application/json")
            from pyweb.runtime.server import Request
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n) if n else b""
            resp = server_runtime.handle(
                Request("POST", self.path.split("?")[0], dict(self.headers), body))
            return self._bytes(resp.body, resp.status,
                               resp.headers.get("Content-Type", "application/json"),
                               {k: v for k, v in resp.headers.items()
                                if k not in ("Content-Type", "Content-Length")})

        def log_message(self, fmt, *args):  # noqa: N802
            if logger is not None:
                logger.info("http %s %s" % (self.command, self.path))

    return H


def serve(dist, *, host="0.0.0.0", port=8000, app_factory=None, logger=None,
          rate_limit=None, rpc_timeout=30.0):
    """Serve ``dist/`` forever. Returns the server (for tests, use
    ``serve_in_thread``).

    RPC protection is on by default: 120 calls/min/IP unless
    ``rate_limit=`` overrides (``False`` disables — tests only).
    """
    from pyweb import rpc as _rpc
    from pyweb.runtime.server import Server
    manifest, static_dir, _ = load_dist(dist)
    if rate_limit is False:
        rate_limit = None
    elif rate_limit is None:
        rate_limit = _rpc.RateLimiter(max_calls=120, window=60.0)
    runtime = None
    if app_factory:
        mod_name, _, attr = app_factory.partition(":")
        import importlib
        mod = importlib.import_module(mod_name or attr and mod_name)
        factory = getattr(mod, attr or "app", None)
        compiled, impls = factory(manifest) if callable(factory) else (None, {})
        runtime = Server(compiled or {"pages": {}, "rpc": []},
                         rate_limit=rate_limit, rpc_timeout=rpc_timeout)
        for fn in (impls or {}).values():
            runtime.register_rpc(fn)
    handler = make_handler(static_dir=static_dir, server_runtime=runtime,
                           logger=logger)
    httpd = ThreadedServer((host, port), handler)
    if logger is not None:
        logger.info(f"serving {dist}", host=host, port=httpd.server_address[1])
    return httpd


def serve_in_thread(dist, **kwargs):
    httpd = serve(dist, host="127.0.0.1", port=0, **kwargs)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread
