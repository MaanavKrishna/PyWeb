"""Server runtime: routing, SSR, typed RPC dispatcher, static assets."""

from __future__ import annotations

import html as _html
import inspect
import json
import re
import uuid


class Request:
    def __init__(self, method, path, headers=None, body=b"", cookies=None, client=None):
        self.method = method.upper()
        self.client = client  # the peer's IP address, when the server knows it
        self.path = path
        self.headers = headers or {}
        self.body = body
        if cookies is None:
            cookies = {}
            raw = next((v for k, v in self.headers.items() if k.lower() == "cookie"), "")
            for pair in (raw or "").split(";"):
                name, sep, value = pair.partition("=")
                if sep and name.strip():
                    cookies[name.strip()] = value.strip().strip('"')
        self.cookies = cookies
        self.id = uuid.uuid4().hex[:12]


class Response:
    def __init__(self, status=200, body="", headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {}


_POOL = None


class RPCStream:
    """The response body of a server function that ``yield``s: one NDJSON line per value.

    Lines are ``{"chunk": value}``, then ``{"done": true}``, or
    ``{"error": {...}}`` if the function raises part-way. Each step runs in
    the request's context (so ``session`` and ``request`` work inside the
    generator). ``close()`` (the client went away, or cancelled) closes the
    generator, so ``finally`` blocks and ``with`` statements in it run.
    Iterate it in a thread (stdlib servers) or ``async for`` over
    :meth:`aiter` (ASGI).
    """

    def __init__(self, gen, name, *, logger=None, request_id=""):
        import contextvars
        import threading
        self.gen, self.name, self.logger, self.request_id = gen, name, logger, request_id
        self.context = contextvars.copy_context()
        self.is_async = inspect.isasyncgen(gen)
        self._loop = None
        self._lock = threading.Lock()
        self._busy = False
        self._closing = False
        self._finished = False

    def _next(self):
        """The next value, or raise StopIteration."""
        if self.is_async:
            import asyncio
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
            try:
                return self.context.run(self._loop.run_until_complete, self.gen.__anext__())
            except StopAsyncIteration:
                raise StopIteration from None
        return self.context.run(next, self.gen)

    def _line(self):
        """The next NDJSON line, or None when the stream is over."""
        from pyweb import rpc as _rpc
        from pyweb.ssr import to_jsonable
        if self._finished or self._closing:
            return None
        with self._lock:
            self._busy = True
        try:
            value = self._next()
            return (json.dumps({"chunk": to_jsonable(value)}) + "\n").encode()
        except StopIteration:
            self._finished = True
            return b'{"done": true}\n'
        except _rpc.RPCError as exc:
            self._finished = True
            return (json.dumps({"error": {"code": exc.code, "message": str(exc),
                                          "details": exc.details or {}}}) + "\n").encode()
        except Exception as exc:  # noqa: BLE001 - reported in-band; the status line is already sent
            self._finished = True
            if self.logger is not None:
                self.logger.error(f"rpc {self.name} failed while streaming: {exc}",
                                  request_id=self.request_id, rpc=self.name)
            return b'{"error": {"code": "internal", "message": "internal server error", "details": {}}}\n'
        finally:
            with self._lock:
                self._busy = False
            if self._closing:
                self._shutdown()

    def __iter__(self):
        try:
            while True:
                line = self._line()
                if line is None:
                    return
                yield line
        finally:
            self.close()

    async def aiter(self):
        import asyncio
        try:
            while True:
                line = await asyncio.to_thread(self._line)
                if line is None:
                    return
                yield line
        finally:
            self.close()

    def snapshot(self):
        """Everything, synchronously (tests)."""
        return b"".join(iter(self))

    def close(self):
        with self._lock:
            self._closing = True
            if self._busy:
                return  # the step in progress closes it when it returns
        self._shutdown()

    def _shutdown(self):
        gen, self.gen = self.gen, None
        if gen is None:
            return
        try:
            if self.is_async:
                if self._loop is not None:
                    self.context.run(self._loop.run_until_complete, gen.aclose())
                    self._loop.close()
            else:
                self.context.run(gen.close)
        except Exception:  # noqa: BLE001 - closing must not raise
            pass


def _rpc_error():
    from pyweb.rpc import RPCError
    return RPCError


async def _awaited(aw):
    return await aw


def _rpc_pool():
    global _POOL
    if _POOL is None:
        import concurrent.futures as _fut
        _POOL = _fut.ThreadPoolExecutor(max_workers=32, thread_name_prefix="pyweb-rpc")
    return _POOL


def _trusted_proxies():
    """How many reverse proxies to trust for X-Forwarded-For (``PYWEB_TRUST_PROXY``)."""
    import os
    raw = os.environ.get("PYWEB_TRUST_PROXY", "").strip().lower()
    if raw in ("", "0", "false", "no"):
        return 0
    if raw in ("1", "true", "yes"):
        return 1
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def client_ip(req, proxies=None):
    """The caller's IP address.

    ``X-Forwarded-For`` is set by whoever sends the request, so it only counts
    when you say a proxy you run sets it (``PYWEB_TRUST_PROXY=1``, or the
    number of proxies in front of the app). Then the address your proxy saw
    is used: the entry that many places from the end.
    """
    proxies = _trusted_proxies() if proxies is None else proxies
    if proxies:
        xff = next((v for k, v in (req.headers or {}).items() if k.lower() == "x-forwarded-for"), "")
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        if len(hops) >= proxies:
            return hops[-proxies]
    return req.client or "unknown"


_SIGNATURES: dict = {}


def _signature(fn):
    sig = _SIGNATURES.get(fn)
    if sig is None:
        sig = _SIGNATURES[fn] = inspect.signature(fn)
    return sig


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
    #: Default cap for RPC request bodies (1 MiB). Bodies beyond this
    #: are rejected with ``413 body-too-large`` before JSON parsing.
    MAX_BODY = 1_048_576

    def __init__(self, compiled=None, *, auth_secret=None, csrf_secret=None,
                 rate_limit=None, rpc_timeout=None, tracer=None,
                 logger=None, max_body=None, app=None, debug=False,
                 secure_cookies=False):
        if compiled is None:
            compiled = app.compiled if app is not None else {"pages": {}, "rpc": []}
        self.app = app
        self.debug = debug
        self.secure_cookies = secure_cookies
        self.compiled = compiled
        self.rpc_impls: dict[str, object] = {}
        self.routes: list[tuple[re.Pattern, str]] = []
        self.error_pages: dict[int, str] = {}
        for name, page in compiled["pages"].items():
            if page.get("error_status"):
                self.error_pages[page["error_status"]] = name
                continue
            pat = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", page["route"]) + "$"
            self.routes.append((re.compile(pat), name))
        # Production RPC controls (all optional; secure defaults when set).
        self.auth_secret = auth_secret
        self.csrf_secret = csrf_secret
        self.rate_limit = rate_limit  # RateLimiter or None
        self.rpc_timeout = rpc_timeout  # seconds or None
        self.max_body = self.MAX_BODY if max_body is None else max_body
        self.tracer = tracer
        self.logger = logger
        if app is not None:
            for fn in app.rpc.values():
                self.register_rpc(fn)

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
        else:  # numeric status with the same envelope shape
            body = {"error": {"code": f"http_{code}", "message": message,
                              "details": details or {}}}
            status = code
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
            ok, retry = self.rate_limit.allow(f"{fn.__name__}:{client_ip(req)}")
            if not ok:
                return self._err(_rpc.Code.RATE_LIMIT, "rate limit exceeded",
                                 retry_after=retry)
        return None

    def _validate(self, fn, args: dict):
        sig = _signature(fn)
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
            hdr = {k.lower(): v for k, v in (req.headers or {}).items()}
            ctype = hdr.get("content-type", "")
            if ctype and "json" not in ctype.lower():
                # Browsers can only send cross-site requests without a CORS
                # preflight using form encodings; requiring JSON blocks them.
                return self._err(415, "RPC requests must be application/json",
                                 trace_id=trace_id, traceparent=traceparent)
            origin = hdr.get("origin")
            host = hdr.get("x-forwarded-host") or hdr.get("host")
            if origin and host and origin != "null":
                from urllib.parse import urlparse as _urlparse
                if _urlparse(origin).netloc != host:
                    return self._err(_rpc.Code.CSRF, "cross-origin RPC call rejected",
                                     trace_id=trace_id, traceparent=traceparent)
            if self.max_body is not None and len(req.body or b"") > self.max_body:
                return self._err(413, "request body too large",
                                 trace_id=trace_id, traceparent=traceparent)
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
            if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn):
                # Streamed: no timeout (it runs as long as it yields), errors arrive in-band.
                stream = RPCStream(fn(**clean), name, logger=self.logger, request_id=trace_id)
                return Response(200, stream, {"Content-Type": "application/x-ndjson", "Cache-Control": "no-store",
                                              "X-Accel-Buffering": "no", "X-Request-Id": trace_id,
                                              "traceparent": traceparent})
            try:
                if self.rpc_timeout:
                    result = self._call_with_timeout(fn, clean)
                else:
                    result = fn(**clean)
                if inspect.isawaitable(result):
                    import asyncio
                    aw = asyncio.wait_for(_awaited(result), self.rpc_timeout) if self.rpc_timeout else _awaited(result)
                    try:
                        result = asyncio.run(aw)
                    except asyncio.TimeoutError as exc:  # a separate class before Python 3.11
                        raise TimeoutError() from exc
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
            from pyweb.ssr import to_jsonable
            try:
                payload = json.dumps({"result": to_jsonable(result)})
            except TypeError as exc:
                if self.logger is not None:
                    self.logger.error(f"rpc {name} returned unserializable value: {exc}",
                                      request_id=trace_id, rpc=name)
                return self._err(_rpc.Code.INTERNAL, "internal server error",
                                 trace_id=trace_id, traceparent=traceparent)
            return Response(200, payload, headers)
        finally:
            if span is not None and self.tracer is not None:
                try:
                    self.tracer.finish(span)
                except Exception:
                    pass

    def _call_with_timeout(self, fn, clean):
        """Run ``fn`` but stop waiting after ``rpc_timeout`` seconds.

        Python cannot kill a thread, so a timed-out call keeps running in
        the background pool; the client gets ``timeout`` immediately.
        """
        import concurrent.futures as _fut
        import contextvars
        ctx = contextvars.copy_context()
        fut = _rpc_pool().submit(ctx.run, fn, **clean)
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
        """Dispatch one request inside a :mod:`pyweb.context` request scope."""
        from pyweb import context as _ctx
        rc = _ctx.RequestContext(req, auth_secret=self.auth_secret,
                                 secure_cookies=self.secure_cookies)
        token = _ctx.activate(rc)
        try:
            resp = self._dispatch(req)
        finally:
            _ctx.deactivate(token)
        if resp is not None and rc.set_cookies:
            existing = resp.headers.get("Set-Cookie")
            cookies = ([existing] if isinstance(existing, str) else list(existing or [])) + rc.set_cookies
            resp.headers["Set-Cookie"] = cookies
        return resp

    def _dispatch(self, req: Request):
        if req.path.startswith("/__pyweb/rpc/"):
            if req.method != "POST":
                return self._err(405, "RPC endpoints accept POST only")
            return self.handle_rpc(req)
        if req.path == "/__pyweb/events" or req.path.startswith("/__pyweb/events?"):
            return self.handle_events(req)
        if req.path == "/__pyweb/poll" or req.path.startswith("/__pyweb/poll?"):
            return self.handle_poll(req)
        path = req.path.split("?")[0]
        for pat, name in self.routes:
            m = pat.match(path)
            if not m:
                continue
            if req.method not in ("GET", "HEAD"):
                return Response(405, "method not allowed", {"Allow": "GET, HEAD",
                                                            "Content-Type": "text/plain"})
            if self.app is None:
                page = self.compiled["pages"][name]
                return Response(200, page["html"], {"Content-Type": "text/html; charset=utf-8",
                                                    "X-Request-Id": req.id})
            return self._render_page(req, name, m.groupdict())
        return self.error_page(404, req)

    def _render_page(self, req, name, params):
        from pyweb.context import BadRequest, NotFound, Redirect
        from urllib.parse import parse_qs, unquote
        path, _, qs = req.path.partition("?")
        try:
            result = self.app.render(name, {k: unquote(v) for k, v in params.items()},
                                     query=parse_qs(qs, keep_blank_values=True), path=path)
        except NotFound:
            return self.error_page(404, req)
        except BadRequest as exc:
            return self.error_page(400, req, message=str(exc))
        except _rpc_error() as exc:
            status = {"not_found": 404, "forbidden": 403, "unauthenticated": 401}.get(exc.code)
            if status is None:
                raise
            return self.error_page(status, req, message=str(exc))
        except Exception as exc:  # noqa: BLE001
            import traceback
            if self.logger is not None:
                self.logger.error(f"page {name} failed: {exc}", request_id=req.id, page=name)
            if self.debug:
                return self.error_page(500, req, title=f"{type(exc).__name__} in {name}()",
                                       message=traceback.format_exc())
            return self.error_page(500, req)
        if isinstance(result, Redirect):
            return Response(result.status, "", {"Location": result.url, "X-Request-Id": req.id})
        return Response(200, result, {"Content-Type": "text/html; charset=utf-8",
                                      "X-Request-Id": req.id,
                                      "Cache-Control": "no-store"})

    def error_page(self, status, req=None, *, title=None, message=None):
        """Branded HTML error shell. Apps override via ``error_pages`` on
        the compiled dict (``{404: html, 500: html}``) or ``register_error``.

        Overrides may use ``{{path}}`` and ``{{request_id}}`` placeholders.
        """
        overrides = (self.compiled.get("error_pages") or {})
        template = overrides.get(status)
        request_id = getattr(req, "id", "") if req is not None else ""
        path = getattr(req, "path", "") if req is not None else ""
        page = self.error_pages.get(status) or (self.error_pages.get(500) if status >= 500 else None)
        if page and template is None and self.app is not None and not (self.debug and status >= 500 and message):
            rendered = self._render_error(page, status, req, message)
            if rendered is not None:
                return rendered
        if template is not None:
            body = template.replace("{{path}}", _html.escape(path)).replace(
                "{{request_id}}", _html.escape(request_id))
            return Response(status, body,
                            {"Content-Type": "text/html",
                             "X-Request-Id": request_id})
        default_title = {404: "Page not found", 500: "Something went wrong"}
        heading = _html.escape(title or default_title.get(status, f"Error {status}"))
        hint = _html.escape(message or ("The page you're looking for doesn't exist. "
                                        if status == 404 else
                                        "Please try again; the error has been logged. "))
        body = (
            "<!doctype html><html lang=en><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{status} {heading}</title>"
            "<body style='font-family:system-ui,sans-serif;max-width:640px;"
            "margin:10vh auto;padding:0 20px;color:#111'>"
            f"<h1>{status} — {heading}</h1>"
            + (f"<pre style='white-space:pre-wrap;background:#f6f6f6;padding:12px;font-size:13px'>{hint}</pre>"
               if message and "\n" in (message or "") else f"<p>{hint}</p>")
            + (f"<p><a href='/'>Back home</a> · "
                f"<code>{_html.escape(path)}</code></p>" if status == 404 else "")
            + (f"<p style='color:#666;font-size:13px'>request id: "
                f"<code>{_html.escape(request_id)}</code></p>" if request_id else "")
            + "</body></html>")
        return Response(status, body, {"Content-Type": "text/html",
                                       "X-Request-Id": request_id})

    def _render_error(self, name, status, req, message):
        """An ``@app.error`` page, or None if it fails too (the built-in page is used instead)."""
        from pyweb.context import Redirect
        path = (getattr(req, "path", "") or "").split("?")[0]
        try:
            html = self.app.render(name, path=path, extra={"status": status, "path": path, "message": message or "",
                                                           "request_id": getattr(req, "id", "")})
        except Exception as exc:  # noqa: BLE001 - never fail while reporting a failure
            if self.logger is not None:
                self.logger.error(f"error page {name} failed: {exc}", page=name)
            return None
        if isinstance(html, Redirect):
            return Response(html.status, "", {"Location": html.url})
        return Response(status, html, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
                                       "X-Request-Id": getattr(req, "id", "")})

    def register_error(self, status, html_template):
        """Register a custom error shell, e.g. ``register_error(404, ...)``."""
        pages = self.compiled.setdefault("error_pages", {})
        pages[status] = html_template

    def _feed(self, req: Request):
        """(bus, channel name, query) for a ``?feed=`` request, or an error Response."""
        from urllib.parse import urlparse, parse_qs
        from pyweb import context as _ctx
        from pyweb import realtime as _rt
        qs = parse_qs(urlparse(req.path).query)
        token = (qs.get("feed") or [""])[0]
        if not token:
            return self._err(400, "missing ?feed= (create one with channel(name) while rendering)")
        feed = _rt.read_feed(token, _ctx._secret(_ctx.current()))
        if feed is None:
            return self._err(403, "invalid or expired feed; reload the page")
        name, start = feed
        spec = (qs.get("live") or [""])[0]
        if spec and name.startswith("pyweb.live:"):
            from pyweb import livedata
            livedata.adopt_spec(spec, _ctx._secret(_ctx.current()))   # keep re-running it here too
        return getattr(self, "bus", None) or _rt.current_bus(), name, start, qs

    def handle_events(self, req: Request):
        """Server-Sent Events: GET /__pyweb/events?feed=TOKEN streams the
        channel's messages, resuming after ``Last-Event-ID``."""
        from pyweb import realtime as _rt
        feed = self._feed(req)
        if isinstance(feed, Response):
            return feed
        bus, name, start, _qs = feed
        try:
            last_id = int(req.headers.get("Last-Event-ID", "0") or 0)
        except ValueError:
            last_id = 0
        last_id = max(last_id, start)
        body = _rt.EventStream(bus, name, last_id)
        if name.startswith("pyweb.live:"):
            from pyweb import livedata
            body = livedata.WatchedStream(body, name[len("pyweb.live:"):])
        return Response(200, body, {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        })

    def handle_poll(self, req: Request):
        """Polling fallback: GET /__pyweb/poll?feed=TOKEN&since=ID."""
        feed = self._feed(req)
        if isinstance(feed, Response):
            return feed
        bus, name, start, qs = feed
        try:
            since = int((qs.get("since") or ["0"])[0])
        except ValueError:
            since = 0
        since = max(since, start)
        messages = [{"id": seq, "data": msg} for seq, msg in bus.since(name, since, limit=100)]
        last_id = messages[-1]["id"] if messages else since
        return Response(200, json.dumps({"messages": messages, "last_id": last_id}),
                        {"Content-Type": "application/json", "Cache-Control": "no-store"})
