"""Production server runtime for PyWeb (Track B).

ASGI app object (no hard dep: stdlib adapter + optional uvicorn when
installed), graceful startup/shutdown, X-Request-Id propagation, structured
JSON access logs, health endpoint, and the middleware chain (security
headers, gzip, signed-cookie sessions, CSRF, rate limiting, static files).
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import json
import os
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

HEALTH_PATH = "/__pyweb/health"

_access_log = sys.stdout


# ---------------------------------------------------------------- Request/Response


class Request:
    def __init__(self, method="GET", path="/", headers=None, query=None,
                 body=b"", client_ip="127.0.0.1", request_id=None, cookies=None):
        self.method = (method or "GET").upper()
        self.path = path or "/"
        self.headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        self.query = dict(query or {})
        self.body = body or b""
        self.client_ip = client_ip or "127.0.0.1"
        self.request_id = request_id or self.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        self.cookies = dict(cookies or {})
        if not self.cookies and self.headers.get("cookie"):
            for part in self.headers["cookie"].split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    self.cookies.setdefault(k.strip(), v.strip())
        self.session: dict = {}
        self._csrf_valid = True

    def json(self):
        if not self.body:
            return {}
        return json.loads(self.body.decode("utf-8"))


class Response:
    def __init__(self, body=b"", status=200, headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.body = body or b""
        self.status = status
        self.headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}

    def text(body: str, status=200, headers=None):
        h = {"content-type": "text/plain; charset=utf-8"}
        h.update({str(k).lower(): str(v) for k, v in (headers or {}).items()})
        return Response(body, status, h)

    def html(body: str, status=200, headers=None):
        h = {"content-type": "text/html; charset=utf-8"}
        h.update({str(k).lower(): str(v) for k, v in (headers or {}).items()})
        return Response(body, status, h)

    def json(data, status=200, headers=None):
        h = {"content-type": "application/json"}
        h.update({str(k).lower(): str(v) for k, v in (headers or {}).items()})
        return Response(json.dumps(data), status, h)


_STATUS_TEXT = {
    200: "OK", 201: "Created", 204: "No Content",
    301: "Moved Permanently", 302: "Found", 304: "Not Modified",
    400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
    404: "Not Found", 405: "Method Not Allowed", 429: "Too Many Requests",
    500: "Internal Server Error",
}


# ---------------------------------------------------------------- access log


def log_access(request: Request, response: Response, duration_ms: float):
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "request_id": request.request_id,
        "method": request.method,
        "path": request.path,
        "status": response.status,
        "bytes": len(response.body),
        "duration_ms": round(duration_ms, 2),
        "client_ip": request.client_ip,
    }
    print(json.dumps(entry), file=_access_log, flush=True)


# ---------------------------------------------------------------- sessions / CSRF


def _sign(value: str, secret: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def sign_cookie(value: str, secret: str) -> str:
    return f"{value}.{_sign(value, secret)}"


def unsign_cookie(signed: str, secret: str) -> str | None:
    if "." not in signed:
        return None
    value, _, sig = signed.rpartition(".")
    if hmac.compare_digest(_sign(value, secret), sig):
        return value
    return None


def new_csrf_token() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


# ---------------------------------------------------------------- middleware


class Middleware:
    def process_request(self, request: Request):
        return None

    def process_response(self, request: Request, response: Response):
        return response


class SecurityHeadersMiddleware(Middleware):
    def __init__(self, csp="default-src 'self'"):
        self.csp = csp

    def process_response(self, request, response):
        response.headers.setdefault("content-security-policy", self.csp)
        response.headers.setdefault("x-frame-options", "DENY")
        response.headers.setdefault("x-content-type-options", "nosniff")
        response.headers.setdefault("referrer-policy", "strict-origin-when-cross-origin")
        return response


class GzipMiddleware(Middleware):
    def __init__(self, min_size: int = 512):
        self.min_size = min_size

    def process_response(self, request, response):
        enc = request.headers.get("accept-encoding", "")
        if "gzip" not in enc or len(response.body) < self.min_size:
            return response
        if response.headers.get("content-encoding"):
            return response
        ctype = response.headers.get("content-type", "")
        if not (ctype.startswith("text/") or "json" in ctype or "html" in ctype
                or "javascript" in ctype or "css" in ctype or "svg" in ctype):
            return response
        response.body = gzip.compress(response.body)
        response.headers["content-encoding"] = "gzip"
        return response


class SessionMiddleware(Middleware):
    def __init__(self, secret: str, cookie_name: str = "pyweb_session"):
        self.secret = secret
        self.cookie_name = cookie_name

    def process_request(self, request):
        raw = request.cookies.get(self.cookie_name, "")
        if raw:
            unsigned = unsign_cookie(raw, self.secret)
            if unsigned:
                try:
                    request.session = json.loads(unsigned)
                except ValueError:
                    request.session = {}
        return None

    def process_response(self, request, response):
        payload = json.dumps(request.session or {})
        signed = sign_cookie(payload, self.secret)
        response.headers["set-cookie"] = (
            f"{self.cookie_name}={signed}; Path=/; HttpOnly; SameSite=Lax"
        )
        return response


class CSRFMiddleware(Middleware):
    SAFE = {"GET", "HEAD", "OPTIONS"}

    def __init__(self, header: str = "x-csrf-token", paths=("__pyweb/rpc",)):
        self.header = header
        self._paths = paths

    def process_request(self, request):
        if request.method in self.SAFE:
            return None
        if not any(p in request.path for p in self._paths):
            return None
        token = request.headers.get(self.header, "")
        session_token = (request.session or {}).get("csrf")
        if not token or not session_token or not hmac.compare_digest(token, session_token):
            request._csrf_valid = False
            return Response.json({"error": "invalid csrf token"}, status=403)
        return None


class RateLimitMiddleware(Middleware):
    def __init__(self, limit: int = 100, window: float = 60.0):
        self.limit = limit
        self.window = window
        self._hits: dict[str, list] = {}

    def process_request(self, request):
        now = time.monotonic()
        hits = [t for t in self._hits.get(request.client_ip, []) if now - t < self.window]
        if len(hits) >= self.limit:
            retry = int(self.window - (now - hits[0])) + 1
            return Response.json(
                {"error": "rate limit exceeded"},
                status=429,
                headers={"retry-after": str(max(retry, 1))},
            )
        hits.append(now)
        self._hits[request.client_ip] = hits
        return None


_MIME = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".json": "application/json",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8", ".woff2": "font/woff2",
}


def hashed_url(path: str, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()[:12]
    base, dot, ext = path.rpartition(".")
    return f"{base}.{digest}.{ext}" if dot else f"{path}.{digest}"


class StaticFilesMiddleware(Middleware):
    """Serve files dict {url_path: bytes} with content-hash immutable caching."""

    def __init__(self, files: dict[str, bytes] | None = None, prefix: str = "/static/"):
        self.prefix = prefix
        self.files: dict[str, bytes] = dict(files or {})
        self.hash_map: dict[str, str] = {}
        for path, content in self.files.items():
            self.hash_map[path] = hashed_url(path, content)

    def url_for(self, path: str) -> str:
        return self.hash_map.get(path, path)

    def process_request(self, request):
        if not request.path.startswith(self.prefix):
            return None
        content = self.files.get(request.path)
        if content is None:
            for plain, hashed in self.hash_map.items():
                if request.path == hashed:
                    content = self.files[plain]
                    break
        if content is None:
            return Response.text("not found", status=404)
        _, dot, ext = request.path.rpartition(".")
        ctype = _MIME.get("." + ext.lower(), "application/octet-stream") if dot else "application/octet-stream"
        etag = '"' + hashlib.sha256(content).hexdigest()[:16] + '"'
        if request.headers.get("if-none-match") == etag:
            return Response(b"", status=304, headers={"etag": etag})
        headers = {"content-type": ctype, "etag": etag}
        if request.path in self.hash_map.values():
            headers["cache-control"] = "public, max-age=31536000, immutable"
        else:
            headers["cache-control"] = "public, max-age=3600"
        return Response(content, 200, headers)


# ---------------------------------------------------------------- application


class Server:
    """Tiny Request/Response dispatcher with middleware, ASGI + stdlib serving."""

    def __init__(self, middlewares: list[Middleware] | None = None):
        self.routes: list = []
        self.rpc_handlers: dict = {}
        self.middlewares: list[Middleware] = list(middlewares or [])
        self._startup_hooks: list = []
        self._shutdown_hooks: list = []
        self.started = False

    # -- routing ------------------------------------------------------
    def route(self, path: str, methods=("GET",)):
        methods = {m.upper() for m in methods}

        def deco(fn):
            self.routes.append((path, methods, fn))
            return fn

        return deco

    def rpc(self, name: str):
        def deco(fn):
            self.rpc_handlers[name] = fn
            return fn

        return deco

    def on_startup(self, fn):
        self._startup_hooks.append(fn)
        return fn

    def on_shutdown(self, fn):
        self._shutdown_hooks.append(fn)
        return fn

    # -- dispatch -----------------------------------------------------
    def _match(self, request: Request):
        for path, methods, fn in self.routes:
            if request.path != path:
                continue
            if request.method not in methods:
                return Response.text("method not allowed", status=405)
            try:
                return fn(request)
            except Exception as exc:  # graceful 500s
                return Response.json({"error": "internal error", "detail": str(exc)}, status=500)
        if request.path == HEALTH_PATH:
            return Response.json({"status": "ok"})
        if request.path.startswith("/__pyweb/rpc/"):
            name = request.path[len("/__pyweb/rpc/"):]
            if request.method != "POST":
                return Response.text("method not allowed", status=405)
            handler = self.rpc_handlers.get(name)
            if handler is None:
                return Response.json({"error": f"unknown rpc: {name}"}, status=404)
            try:
                payload = request.json()
            except ValueError:
                return Response.json({"error": "invalid json"}, status=400)
            try:
                args = payload.get("args", []) if isinstance(payload, dict) else []
                kwargs = payload.get("kwargs", {}) if isinstance(payload, dict) else {}
                result = handler(*args, **kwargs)
                return Response.json({"ok": True, "result": result})
            except TypeError as exc:
                return Response.json({"error": f"bad rpc args: {exc}"}, status=400)
            except Exception as exc:
                return Response.json({"error": "internal error", "detail": str(exc)}, status=500)
        return Response.text("not found", status=404)

    def handle(self, request: Request) -> Response:
        start = time.monotonic()
        for mw in self.middlewares:
            early = mw.process_request(request)
            if early is not None:
                response = early
                break
        else:
            response = self._match(request)
        for mw in reversed(self.middlewares):
            response = mw.process_response(request, response) or response
        response.headers.setdefault("x-request-id", request.request_id)
        log_access(request, response, (time.monotonic() - start) * 1000)
        return response

    # -- lifecycle ----------------------------------------------------
    def startup(self):
        for hook in self._startup_hooks:
            hook()
        self.started = True

    def shutdown(self):
        for hook in reversed(self._shutdown_hooks):
            hook()
        self.started = False

    # -- ASGI ----------------------------------------------------------
    def asgi(self):
        server = self

        async def app(scope, receive, send):
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        try:
                            server.startup()
                        except Exception as exc:  # noqa: BLE001
                            await send({"type": "lifespan.startup.failed", "message": str(exc)})
                            return
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        try:
                            server.shutdown()
                        except Exception as exc:  # noqa: BLE001
                            await send({"type": "lifespan.shutdown.failed", "message": str(exc)})
                            return
                        await send({"type": "lifespan.shutdown.complete"})
                        return
            if scope["type"] != "http":
                return
            body = b""
            while True:
                msg = await receive()
                body += msg.get("body", b"")
                if not msg.get("more_body"):
                    break
            headers = {
                k.decode().lower(): v.decode()
                for k, v in scope.get("headers", [])
            }
            query = {k: v[0] if len(v) == 1 else v for k, v in
                     parse_qs(scope.get("query_string", b"").decode()).items()}
            client = scope.get("client")
            request = Request(
                method=scope.get("method", "GET"),
                path=scope.get("path", "/"),
                headers=headers,
                query=query,
                body=body,
                client_ip=client[0] if client else "127.0.0.1",
                request_id=headers.get("x-request-id") or uuid.uuid4().hex[:12],
            )
            response = server.handle(request)
            raw = [(k.encode(), str(v).encode()) for k, v in response.headers.items()]
            ctype = response.headers.get("content-type", "application/octet-stream")
            if not any(k == b"content-type" for k, _ in raw):
                raw.append((b"content-type", ctype.encode()))
            await send({"type": "http.response.start",
                        "status": response.status, "headers": raw})
            await send({"type": "http.response.body", "body": response.body})

        return app

    # -- stdlib adapter -------------------------------------------------
    def wsgi_like_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _run(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                path, _, qs = self.path.partition("?")
                request = Request(
                    method=self.command,
                    path=path,
                    headers=dict(self.headers.items()),
                    query={k: v[0] if len(v) == 1 else v
                           for k, v in parse_qs(qs).items()},
                    body=body,
                    client_ip=self.client_address[0],
                    request_id=self.headers.get("X-Request-Id") or uuid.uuid4().hex[:12],
                )
                response = server.handle(request)
                text = _STATUS_TEXT.get(response.status, "OK")
                self.send_response(response.status, text)
                for k, v in response.headers.items():
                    self.send_header(k, str(v))
                self.send_header("Content-Length", str(len(response.body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(response.body)

            do_GET = _run
            do_POST = _run
            do_PUT = _run
            do_DELETE = _run
            do_HEAD = _run
            do_OPTIONS = _run
            do_PATCH = _run

        return Handler

    def serve(self, host="127.0.0.1", port=8000):
        self.startup()
        httpd = HTTPServer((host, port), self.wsgi_like_handler())
        try:
            print(f"PyWeb serving on http://{host}:{port}", flush=True)
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            try:
                httpd.server_close()
            finally:
                self.shutdown()

    def run(self, host="127.0.0.1", port=8000, backend: str = "auto"):
        """Serve with uvicorn when available, else the stdlib adapter."""
        if backend in ("auto", "uvicorn"):
            try:
                import uvicorn  # type: ignore
            except ImportError:
                if backend == "uvicorn":
                    raise RuntimeError(
                        "uvicorn backend requested but 'uvicorn' is not "
                        "installed. Install it with: pip install uvicorn"
                    )
            else:
                uvicorn.run(self.asgi(), host=host, port=port, log_level="warning")
                return
        self.serve(host, port)
