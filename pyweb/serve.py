"""Production server: threaded stdlib HTTP over a built ``dist/``.

``pyweb build`` copies the app source into ``dist/app.pyweb``; ``serve``
loads it (:class:`pyweb.app_loader.LoadedApp`), so ``@server`` functions
are live RPC endpoints and pages render per request with real data. It
serves hashed static assets with immutable caching and answers
``/healthz`` for orchestrators.

For process managers and HTTP/2, prefer the ASGI adapter
(:mod:`pyweb.asgi`) under uvicorn/gunicorn; this server is dependency-free
and fine behind a reverse proxy for small and medium deployments.
``--app mod:attr`` (legacy) mounts RPC implementations from a factory
returning ``(compiled, rpc_impls)``.
"""

from __future__ import annotations

import http.server
import json
import os
import re
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


def _env_int(name, default):
    from pyweb.config import settings
    field = {"PYWEB_MAX_CONNECTIONS": "max_connections", "PYWEB_SOCKET_TIMEOUT": "socket_timeout"}[name]
    return getattr(settings(), field)


class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """One thread per connection, at most ``PYWEB_MAX_CONNECTIONS`` (default 256) at once.

    Over the cap, a connection gets an immediate 503 instead of a thread, so a
    flood of connections can't exhaust the process.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        self.max_connections = min(_env_int("PYWEB_MAX_CONNECTIONS", 256), 1024)   # one thread each
        self._slots = threading.BoundedSemaphore(self.max_connections)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n"
                                b"Retry-After: 1\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


#: Request headers larger than this in total get a 431.
MAX_HEADER_BYTES = 16 * 1024


class LimitedHandler(http.server.BaseHTTPRequestHandler):
    """Request handler with a socket timeout and a cap on header size.

    The timeout (``PYWEB_SOCKET_TIMEOUT``, default 30 s) drops clients that
    send a request too slowly or stop reading, so they can't hold threads.
    """

    timeout = _env_int("PYWEB_SOCKET_TIMEOUT", 30)

    def parse_request(self):
        if not super().parse_request():
            return False
        size = sum(len(k) + len(v) + 4 for k, v in self.headers.items())
        if size > MAX_HEADER_BYTES:
            self.close_connection = True
            self.send_error(431, "Request Header Fields Too Large")
            return False
        return True


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
#: Content-Security-Policy for HTML responses (override with PYWEB_CSP).
#: Scripts only from this origin; the page-state JSON block is data, not code.
# No inline script ever runs; inline <style> blocks only by their hash (csp_for), so injected
# markup can't add a stylesheet (CSS can leak a page's secrets). style="..." attributes stay
# allowed: markup's style={...} and components use them.
DEFAULT_CSP = ("default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'self'; "
               "frame-ancestors 'self'; form-action 'self'; img-src 'self' data: https:; "
               "style-src 'self' https:; style-src-attr 'unsafe-inline'; font-src 'self' data: https:; "
               "connect-src 'self'; worker-src 'self' blob:")

def _security_headers():
    from pyweb.hosting import secure_from_env, security_headers
    return dict(security_headers(secure_from_env()))


SECURITY_HEADERS = _security_headers()


def csp_for(csp, html):
    """``csp`` allowing the page's own inline import map (npm packages) and ``<style>`` blocks by
    their hashes: exactly those, so anything injected into the page still doesn't run."""
    import base64
    import hashlib

    def sha(data):
        return "'sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode() + "'"

    def add(policy, directive, hashes):
        if not hashes or not re.search(rf"(^|;)\s*{directive}\s", policy):
            return policy
        if "'unsafe-inline'" in re.search(rf"{directive} ([^;]*)", policy).group(1):
            return policy                  # hashes would switch 'unsafe-inline' off: leave a custom policy be
        return re.sub(rf"{directive} ([^;]*)", lambda x: f"{directive} {x.group(1)} {' '.join(hashes)}",
                      policy, count=1)

    body = html if isinstance(html, bytes) else html.encode()
    m = re.search(rb'<script type="importmap">(.*?)</script>', body, re.S)
    csp = add(csp, "script-src", [sha(m.group(1))] if m else [])
    styles = list(dict.fromkeys(sha(s) for s in re.findall(rb"<style(?:\s[^>]*)?>(.*?)</style>", body, re.S)))
    return add(csp, "style-src", styles)


def make_handler(*, static_dir, server_runtime=None, logger=None,
                 started_at=None, max_body=MAX_BODY):
    started = started_at or time.time()

    class H(LimitedHandler):
        server_version = f"PyWeb/{VERSION}"

        def _bytes(self, body, status=200, ctype="text/plain", extra=None):
            raw = body.encode() if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            merged = _security_headers()
            if ctype.startswith("text/html"):
                merged["Content-Security-Policy"] = csp_for(os.environ.get("PYWEB_CSP", DEFAULT_CSP), raw)
            merged.update(extra or {})
            if status == 200:
                from pyweb.hosting import gzip_response
                pairs, raw = gzip_response([("Content-Type", ctype), *merged.items()], raw,
                                           self.headers.get("Accept-Encoding"))
                merged = dict(pairs[1:])
            self.send_header("Content-Length", str(len(raw)))
            for k, v in merged.items():
                for item in (v if isinstance(v, list) else [v]):
                    self.send_header(k, item)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)

        def _static(self, rel, query=""):
            from pyweb.hosting import static_file
            status, headers, body = static_file([static_dir], rel, query=query,
                                                if_none_match=self.headers.get("If-None-Match"))
            hdrs = dict(headers)
            return self._bytes(body, status, hdrs.pop("Content-Type"), hdrs)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            if path in ("/healthz", "/health", "/readyz"):
                from pyweb.hosting import health
                code, payload = health(path == "/readyz", started)
                return self._bytes(json.dumps(payload), code, "application/json", {"Cache-Control": "no-store"})
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):], self.path.partition("?")[2])
            if server_runtime is not None:
                from pyweb.runtime.server import Request
                resp = server_runtime.handle(Request(
                    "GET", self.path, dict(self.headers),
                    cookies=parse_cookies(self.headers.get("Cookie")), client=self.client_address[0]))
                if hasattr(resp.body, "snapshot"):  # Server-Sent Events
                    from pyweb.hosting import secure_from_env, security_headers, write_http
                    _sec = security_headers(secure_from_env())
                    hdrs = list(resp.headers.items()) + [h for h in _sec if h[0] not in resp.headers]
                    return write_http(self, resp.status, hdrs, resp.body, head=self.command == "HEAD")
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
            from pyweb.hosting import body_limit
            limit = body_limit(self.path, max_body)
            if n > limit:
                clean = self._drain(n)
                extra = {} if clean else {"Connection": "close"}
                self._bytes(json.dumps(
                    {"ok": False, "error": "body-too-large",
                     "message": f"request body exceeds {limit} bytes"}),
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
                     "message": "this dist has no app.pyweb; rebuild with `pyweb build`"}),
                    501, "application/json")
            from pyweb.runtime.server import Request
            resp = server_runtime.handle(Request(
                "POST", self.path.split("?")[0], dict(self.headers), body,
                cookies=parse_cookies(self.headers.get("Cookie")), client=self.client_address[0]))
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
          csrf_secret=None, max_body=1_048_576, migrate=False):
    """Serve ``dist/`` forever. Returns the server (for tests, use
    ``serve_in_thread``).

    RPC protection is on by default: 120 calls/min/IP unless
    ``rate_limit=`` overrides (``False`` disables — tests only).
    ``auth_secret``/``csrf_secret`` default to ``PYWEB_AUTH_SECRET`` /
    ``PYWEB_CSRF_SECRET`` env vars; without a secret, auth decorators
    stay advisory (sessions cannot verify). ``max_body`` caps request
    bodies (413 beyond it). ``migrate=True`` applies pending migrations
    (under a lock shared by every server) before serving.
    """
    from pyweb import config as _config
    from pyweb import rpc as _rpc
    from pyweb.runtime.server import Server
    _config.startup()
    manifest, static_dir, _ = load_dist(dist)
    if rate_limit is False:
        rate_limit = None
    elif rate_limit is None:
        rate_limit = _rpc.default_rate_limiter()
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
    app_source = os.path.join(dist, manifest.get("app") or "app.pyweb")
    if not app_factory and os.path.isfile(app_source):
        from pyweb.app_loader import LoadedApp
        asset_urls = {name: f"/static/{p['js']}" for name, p in {**manifest.get("pages", {}), **manifest.get("layouts", {})}.items()
                      if p.get("js")}
        app = LoadedApp(app_source, asset_urls=asset_urls)
        if migrate or os.environ.get("PYWEB_MIGRATE_ON_START", "").lower() in ("1", "true", "yes"):
            applied = app.migrate()
            if logger is not None:
                logger.info("migrations: " + (", ".join(applied) if applied else "up to date"))
        secure = os.environ.get("PYWEB_COOKIE_SECURE", "").lower() in ("1", "true")
        runtime = Server(app=app, rate_limit=rate_limit, rpc_timeout=rpc_timeout,
                         auth_secret=auth_secret, csrf_secret=csrf_secret,
                         max_body=max_body, logger=logger, secure_cookies=secure)
    elif app_factory:
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
        from pyweb.hosting import shutdown
        name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        if logger is not None:
            logger.info(f"received {name}: draining in-flight requests")
        shutdown(min(timeout, 10.0), logger=logger)
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
