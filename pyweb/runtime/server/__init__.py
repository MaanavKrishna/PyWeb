"""Server runtime: routing, SSR, typed RPC dispatcher, static assets."""

from __future__ import annotations

import html as _html
import inspect
import json
import re
import uuid


class Request:
    def __init__(self, method, path, headers=None, body=b"", cookies=None):
        self.method = method.upper()
        self.path = path
        self.headers = headers or {}
        self.body = body
        self.cookies = cookies or {}
        self.id = uuid.uuid4().hex[:12]


class Response:
    def __init__(self, status=200, body="", headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {}


def _coerce(value, ann):
    if ann in ("int", "Integer"):
        return int(value)
    if ann in ("float", "Float", "Decimal"):
        return float(value)
    if ann in ("bool", "Boolean"):
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes")
        return bool(value)
    return value


class Server:
    def __init__(self, compiled, *, auth_secret=None, csrf_secret=None,
                 rate_limit=None, rpc_timeout=None, tracer=None,
                 logger=None):
        self.compiled = compiled
        self.rpc_impls: dict[str, object] = {}
        self.routes: list[tuple[re.Pattern, str]] = []
        for name, page in compiled["pages"].items():
            pat = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", page["route"]) + "$"
            self.routes.append((re.compile(pat), name))
        # Production RPC controls (all optional; secure defaults when set).
        self.auth_secret = auth_secret
        self.csrf_secret = csrf_secret
        self.rate_limit = rate_limit  # RateLimiter or None
        self.rpc_timeout = rpc_timeout  # seconds or None
        self.tracer = tracer
        self.logger = logger

    def register_rpc(self, fn):
        self.rpc_impls[fn.__name__] = fn

    def _trace_ctx(self, req: Request):
        from pyweb import rpc as _rpc
        parsed = _rpc.parse_traceparent(req.headers.get("traceparent"))
        trace_id = parsed[0] if parsed else uuid.uuid4().hex
        span_id = uuid.uuid4().hex[:16]
        return trace_id, _rpc.make_traceparent(trace_id, span_id)

    def _err(self, code, message, *, details=None, trace_id=None,
             traceparent=None, retry_after=None):
        from pyweb import rpc as _rpc
        if isinstance(code, str):
            body = {"error": {"code": code, "message": message,
                              "details": details or {}}}
            status = _rpc.status_for(code)
        else:  # legacy numeric path kept for compat
            status = code
            body = {"error": message}
        headers = {"Content-Type": "application/json"}
        if trace_id:
            headers["X-Request-Id"] = trace_id
        if traceparent:
            headers["traceparent"] = traceparent
        if retry_after:
            headers["Retry-After"] = str(retry_after)
        return Response(status, json.dumps(body), headers)

    def _check_rpc_access(self, req: Request, fn):
        """Auth + CSRF + rate-limit gate. Returns None (allow) or Response."""
        from pyweb import rpc as _rpc
        need_auth = getattr(fn, "__pyweb_auth__", False)
        need = getattr(fn, "__pyweb_permissions__", [])
        csrf_exempt = getattr(fn, "__pyweb_csrf_exempt__", False)
        if need_auth or need:
            secret = self.auth_secret
            if not secret:
                return None  # no secret configured: decorators are advisory
            from pyweb import auth as _auth
            session = _auth.session_from_request(req, secret)
            if session is None or "sub" not in session:
                return self._err(_rpc.Code.AUTH, "authentication required")
            if need and not _auth.can(session.get("roles", []), fn):
                return self._err(_rpc.Code.FORBIDDEN,
                                 f"missing permission: {need}")
            if self.csrf_secret and not csrf_exempt:
                token = req.headers.get("X-CSRF-Token", "")
                if not _auth.verify_csrf(self.csrf_secret,
                                         session.get("sid", session.get("sub", "")),
                                         token):
                    return self._err(_rpc.Code.CSRF, "invalid CSRF token")
        if self.rate_limit is not None:
            key = req.headers.get("X-Forwarded-For",
                                  req.cookies.get("pyweb_session", "anon"))
            ok, retry = self.rate_limit.allow(
                f"{fn.__name__}:{key}")
            if not ok:
                return self._err(_rpc.Code.RATE_LIMIT, "rate limit exceeded",
                                 retry_after=retry)
        return None

    def _validate(self, fn, args: dict):
        sig = inspect.signature(fn)
        out = {}
        for pname, param in sig.parameters.items():
            ann = getattr(param.annotation, "__name__", str(param.annotation)) if param.annotation is not inspect.Parameter.empty else "Any"
            if pname in args:
                try:
                    out[pname] = _coerce(args[pname], ann)
                except (ValueError, TypeError):
                    raise TypeError(f"{fn.__name__}.{pname} expects {ann}")
                if "Email" in ann and "@" not in str(out[pname]):
                    raise ValueError(f"{fn.__name__}.{pname} must be a valid email")
            elif param.default is inspect.Parameter.empty:
                raise TypeError(f"{fn.__name__} missing required argument {pname!r}")
        return out

    def handle_rpc(self, req: Request):
        from pyweb import rpc as _rpc
        m = re.match(r"^/__pyweb/rpc/(\w+)$", req.path)
        if not m:
            return None
        trace_id, traceparent = self._trace_ctx(req)
        span = None
        if self.tracer is not None:
            span = self.tracer.start(f"rpc.{m.group(1)}", trace_id=trace_id)
        try:
            name = m.group(1)
            fn = self.rpc_impls.get(name)
            if fn is None:
                return self._err(_rpc.Code.NOT_FOUND, f"unknown rpc {name}",
                                 trace_id=trace_id, traceparent=traceparent)
            gate = self._check_rpc_access(req, fn)
            if gate is not None:
                gate.headers.setdefault("X-Request-Id", trace_id)
                gate.headers["traceparent"] = traceparent
                return gate
            try:
                payload = json.loads(req.body or b"{}")
            except json.JSONDecodeError:
                return self._err(_rpc.Code.VALIDATION, "invalid JSON",
                                 trace_id=trace_id, traceparent=traceparent)
            args = payload.get("args", {}) if isinstance(payload, dict) else {}
            try:
                clean = self._validate(fn, args if isinstance(args, dict) else {})
            except (TypeError, ValueError) as exc:
                return self._err(_rpc.Code.VALIDATION, str(exc),
                                 trace_id=trace_id, traceparent=traceparent)
            try:
                if self.rpc_timeout:
                    result = self._call_with_timeout(fn, clean)
                else:
                    result = fn(**clean)
            except TimeoutError:
                return self._err(_rpc.Code.TIMEOUT,
                                 f"{name} exceeded {self.rpc_timeout}s",
                                 trace_id=trace_id, traceparent=traceparent)
            except _rpc.RPCError as exc:
                return self._err(exc.code, str(exc), details=exc.details,
                                 trace_id=trace_id, traceparent=traceparent)
            except (TypeError, ValueError) as exc:
                return self._err(_rpc.Code.VALIDATION, str(exc),
                                 trace_id=trace_id, traceparent=traceparent)
            except Exception as exc:  # noqa: BLE001
                if self.logger is not None:
                    self.logger.error(f"rpc {name} failed: {exc}",
                                      request_id=trace_id, rpc=name)
                return self._err(_rpc.Code.INTERNAL,
                                 "internal server error",
                                 trace_id=trace_id, traceparent=traceparent)
            headers = {"Content-Type": "application/json",
                       "X-Request-Id": trace_id, "traceparent": traceparent}
            if isinstance(result, dict) and result.get("__pyweb_stream__"):
                return self._stream_response(result["chunks"], headers)
            return Response(200, json.dumps({"result": result}), headers)
        finally:
            if span is not None and self.tracer is not None:
                try:
                    self.tracer.finish(span)
                except Exception:
                    pass

    def _call_with_timeout(self, fn, clean):
        import concurrent.futures as _fut
        with _fut.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(fn, **clean)
            try:
                return fut.result(timeout=self.rpc_timeout)
            except _fut.TimeoutError as exc:
                raise TimeoutError() from exc

    def _stream_response(self, chunks, headers):
        """NDJSON stream: one {"chunk": ...} object per line."""
        headers = dict(headers)
        headers["Content-Type"] = "application/x-ndjson"
        lines = "".join(json.dumps({"chunk": c}) + "\n" for c in chunks)
        return Response(200, lines, headers)

    def handle(self, req: Request):
        if req.path.startswith("/__pyweb/rpc/"):
            return self.handle_rpc(req)
        if req.path == "/__pyweb/events" or req.path.startswith("/__pyweb/events?"):
            return self.handle_events(req)
        if req.path == "/__pyweb/poll" or req.path.startswith("/__pyweb/poll?"):
            return self.handle_poll(req)
        for pat, name in self.routes:
            m = pat.match(req.path)
            if m:
                page = self.compiled["pages"][name]
                return Response(200, page["html"], {"Content-Type": "text/html", "X-Request-Id": req.id})
        return self.error_page(404, req)

    def error_page(self, status, req=None, *, title=None, message=None):
        """Branded HTML error shell. Apps override via ``error_pages`` on
        the compiled dict (``{404: html, 500: html}``) or ``register_error``.

        Overrides may use ``{{path}}`` and ``{{request_id}}`` placeholders.
        """
        overrides = (self.compiled.get("error_pages") or {})
        template = overrides.get(status)
        request_id = getattr(req, "id", "") if req is not None else ""
        path = getattr(req, "path", "") if req is not None else ""
        if template is not None:
            body = template.replace("{{path}}", _html.escape(path)).replace(
                "{{request_id}}", _html.escape(request_id))
            return Response(status, body,
                            {"Content-Type": "text/html",
                             "X-Request-Id": request_id})
        default_title = {404: "Page not found", 500: "Something went wrong"}
        heading = title or default_title.get(status, f"Error {status}")
        hint = message or ("The page you're looking for doesn't exist. "
                           if status == 404 else
                           "Please try again; the error has been logged. ")
        body = (
            "<!doctype html><html lang=en><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{status} {heading}</title>"
            "<body style='font-family:system-ui,sans-serif;max-width:640px;"
            "margin:10vh auto;padding:0 20px;color:#111'>"
            f"<h1>{status} — {heading}</h1><p>{hint}</p>"
            + (f"<p><a href='/'>Back home</a> · "
                f"<code>{_html.escape(path)}</code></p>" if status == 404 else "")
            + (f"<p style='color:#666;font-size:13px'>request id: "
                f"<code>{_html.escape(request_id)}</code></p>" if request_id else "")
            + "</body></html>")
        return Response(status, body, {"Content-Type": "text/html",
                                       "X-Request-Id": request_id})

    def register_error(self, status, html_template):
        """Register a custom error shell, e.g. ``register_error(404, ...)``."""
        pages = self.compiled.setdefault("error_pages", {})
        pages[status] = html_template

    def handle_events(self, req: Request):
        """SSE stream: GET /__pyweb/events?channel=NAME replays missed frames
        (via Last-Event-ID) then emits a live snapshot. Real servers hold the
        connection open; this runtime returns buffered frames so tests and
        simple deployments work without streaming infrastructure."""
        from urllib.parse import urlparse, parse_qs
        from pyweb import realtime as _rt
        qs = parse_qs(urlparse(req.path).query)
        channel_name = (qs.get("channel") or [""])[0]
        if not channel_name:
            return Response(400, json.dumps({"error": "missing ?channel="}),
                            {"Content-Type": "application/json"})
        last_id = int(req.headers.get("Last-Event-ID", "0") or 0)
        bus = getattr(self, "bus", None) or _rt._default_bus
        frames = "".join(
            _rt.sse_format(seq, channel_name, msg)
            for seq, msg in bus.since(channel_name, last_id))
        return Response(200, frames, {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Channel": channel_name,
            "X-Last-Id": str(bus._seq),
        })

    def handle_poll(self, req: Request):
        """Polling fallback: GET /__pyweb/poll?channel=NAME&since=ID."""
        from urllib.parse import urlparse, parse_qs
        from pyweb import realtime as _rt
        qs = parse_qs(urlparse(req.path).query)
        channel_name = (qs.get("channel") or [""])[0]
        if not channel_name:
            return Response(400, json.dumps({"error": "missing ?channel="}),
                            {"Content-Type": "application/json"})
        try:
            since = int((qs.get("since") or ["0"])[0])
        except ValueError:
            since = 0
        bus = getattr(self, "bus", None) or _rt._default_bus
        messages = [{"id": seq, "channel": channel_name, "data": msg}
                    for seq, msg in bus.since(channel_name, since)]
        return Response(200, json.dumps({"messages": messages, "last_id": bus._seq}),
                        {"Content-Type": "application/json"})
