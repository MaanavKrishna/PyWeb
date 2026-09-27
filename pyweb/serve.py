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

def _version():
    try:
        from pyweb import __version__ as _v
        return _v
    except ImportError:  # pragma: no cover - serve imported standalone
        return "0.0.0+unknown"


VERSION = _version()


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


def parse_cookies(header_value):
    """Parse a ``Cookie`` header into a dict. Malformed pairs are skipped
    (never raise on attacker-controlled input)."""
    cookies = {}
    for pair in (header_value or "").split(";"):
        name, sep, value = pair.partition("=")
        name = name.strip()
        if sep and name:
            cookies[name] = value.strip().strip('"')
    return cookies


#: Default cap for request bodies (1 MiB). Override per-call with
#: ``serve(..., max_body=...)`` or ``make_handler(..., max_body=...)``.
MAX_BODY = 1_048_576

#: Security headers applied to every response. The app's own values win
#: on conflict (merged with ``setdefault`` semantics per header).
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "SAMEORIGIN",
}


def make_handler(*, static_dir, server_runtime=None, logger=None,
                 started_at=None, max_body=MAX_BODY):
    started = started_at or time.time()

    class H(http.server.BaseHTTPRequestHandler):
        server_version = f"PyWeb/{VERSION}"

        def _bytes(self, body, status=200, ctype="text/plain", extra=None):
            raw = body.encode() if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            merged = dict(SECURITY_HEADERS)
            merged.update(extra or {})
            for k, v in merged.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
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
                resp = server_runtime.handle(Request(
                    "GET", path, dict(self.headers),
                    cookies=parse_cookies(self.headers.get("Cookie"))))
                return self._bytes(resp.body, resp.status,
                                   resp.headers.get("Content-Type", "text/html"),
                                   {k: v for k, v in resp.headers.items()
                                    if k != "Content-Type"})
            # Static fallback: pre-rendered page shell if present.
            return self._bytes("not found", 404)

        def _drain(self, n, limit=16_777_216):
            """Discard up to ``n`` request-body bytes in bounded chunks.

            HTTP/1.1 keep-alive requires the server to consume the declared
            body before responding on the same connection; answering 413
            while bytes are still in flight RSTs the socket and the client
            sees a broken pipe instead of the rejection. Discarding (not
            buffering) keeps memory flat. Beyond ``limit`` (16 MiB) the
            client is told to go away with the connection closed.
            """
            left = min(n, limit)
            while left > 0:
                chunk = self.rfile.read(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
            return n <= limit

        def _read_body(self):
            """Read the request body, enforcing ``max_body``. Returns bytes,
            or sends 413 and returns None."""
            try:
                n = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                n = 0
            if n < 0:
                n = 0
            if n > max_body:
                clean = self._drain(n)
                extra = {} if clean else {"Connection": "close"}
                self._bytes(json.dumps(
                    {"ok": False, "error": "body-too-large",
                     "message": f"request body exceeds {max_body} bytes"}),
                    413, "application/json", extra)
                if not clean:
                    try:
                        self.close_connection = True
                    except AttributeError:
                        pass
                return None
            return self.rfile.read(n) if n else b""

        def do_HEAD(self):  # noqa: N802
            # Same routing as GET; _bytes suppresses the body but keeps
            # Content-Length so link checkers/crawlers get real metadata.
            self.do_GET()

        def do_POST(self):  # noqa: N802
            body = self._read_body()
            if body is None:
                return
            if server_runtime is None:
                return self._bytes(json.dumps(
                    {"ok": False, "error": "rpc-unavailable",
                     "message": "serve has no --app factory; "
                                "run with live RPC impls or use pyweb dev"}),
                    501, "application/json")
            from pyweb.runtime.server import Request
            resp = server_runtime.handle(Request(
                "POST", self.path.split("?")[0], dict(self.headers), body,
                cookies=parse_cookies(self.headers.get("Cookie"))))
            return self._bytes(resp.body, resp.status,
                               resp.headers.get("Content-Type", "application/json"),
                               {k: v for k, v in resp.headers.items()
                                if k not in ("Content-Type", "Content-Length")})

        def log_message(self, fmt, *args):  # noqa: N802
            if logger is not None:
                logger.info("http %s %s" % (self.command, self.path))

    return H


def load_factory(app_factory):
    """Import ``module:attr`` and return the factory. Raises RuntimeError
    with an actionable message instead of a bare traceback."""
    mod_name, sep, attr = (app_factory or "").partition(":")
    if not sep or not mod_name or not attr:
        raise RuntimeError(
            f"bad --app {app_factory!r}: expected module:attr, "
            "e.g. --app myapp:factory where factory(manifest) returns "
            "(compiled, rpc_impls)")
    import importlib
    try:
        mod = importlib.import_module(mod_name)
    except ImportError as exc:
        raise RuntimeError(
            f"bad --app {app_factory!r}: cannot import {mod_name!r} "
            f"({exc}); is it on PYTHONPATH?") from exc
    factory = getattr(mod, attr, None)
    if not callable(factory):
        raise RuntimeError(
            f"bad --app {app_factory!r}: {mod_name}.{attr} is not callable; "
            "it must be factory(manifest) -> (compiled, rpc_impls)")
    return factory


def serve(dist, *, host="0.0.0.0", port=8000, app_factory=None, logger=None,
          rate_limit=None, rpc_timeout=30.0, auth_secret=None,
          csrf_secret=None, max_body=1_048_576):
    """Serve ``dist/`` forever. Returns the server (for tests, use
    ``serve_in_thread``).

    RPC protection is on by default: 120 calls/min/IP unless
    ``rate_limit=`` overrides (``False`` disables — tests only).
    ``auth_secret``/``csrf_secret`` default to ``PYWEB_AUTH_SECRET`` /
    ``PYWEB_CSRF_SECRET`` env vars; without a secret, auth decorators
    stay advisory (sessions cannot verify). ``max_body`` caps request
    bodies (413 beyond it).
    """
    from pyweb import rpc as _rpc
    from pyweb.runtime.server import Server
    manifest, static_dir, _ = load_dist(dist)
    if rate_limit is False:
        rate_limit = None
    elif rate_limit is None:
        rate_limit = _rpc.RateLimiter(max_calls=120, window=60.0)
    if auth_secret is None:
        auth_secret = os.environ.get("PYWEB_AUTH_SECRET")
    if csrf_secret is None:
        csrf_secret = os.environ.get("PYWEB_CSRF_SECRET")
    if logger is not None and auth_secret:
        from pyweb import auth as _auth
        warning = _auth.warn_if_insecure_cookies(
            os.environ.get("PYWEB_COOKIE_SECURE", "").lower() in ("1", "true"),
            host=host)
        if warning is not None:
            logger.info(f"security warning: {warning}")
    runtime = None
    if app_factory:
        factory = load_factory(app_factory)
        compiled, impls = factory(manifest) if callable(factory) else (None, {})
        runtime = Server(compiled or {"pages": {}, "rpc": []},
                         rate_limit=rate_limit, rpc_timeout=rpc_timeout,
                         auth_secret=auth_secret, csrf_secret=csrf_secret,
                         max_body=max_body)
        for fn in (impls or {}).values():
            runtime.register_rpc(fn)
    handler = make_handler(static_dir=static_dir, server_runtime=runtime,
                           logger=logger, max_body=max_body)
    httpd = ThreadedServer((host, port), handler)
    if logger is not None:
        logger.info(f"serving {dist}", host=host, port=httpd.server_address[1])
    return httpd


def install_shutdown_handlers(httpd, *, logger=None, timeout=25.0):
    """Handle SIGTERM/SIGINT by draining in-flight requests, then exiting.

    Container orchestrators (k8s, Docker, Fly) send SIGTERM before SIGKILL;
    without this, in-flight RPCs die mid-write. Returns the previous
    handlers so tests can restore them. Main-thread only — silently skips
    elsewhere (threads cannot receive process signals).
    """
    import signal
    prev = {}

    def _drain(signum, _frame):
        name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        if logger is not None:
            logger.info(f"received {name}: draining in-flight requests")
        t = threading.Thread(target=httpd.shutdown, daemon=True,
                             name="pyweb-shutdown")
        t.start()
        t.join(timeout)

    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            prev[sig] = signal.getsignal(sig)
            signal.signal(sig, _drain)
    except ValueError:
        pass  # not the main thread: process signals unavailable
    return prev


def serve_in_thread(dist, **kwargs):
    httpd = serve(dist, host="127.0.0.1", port=0, **kwargs)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread
