"""Server runtime: routing, SSR, typed RPC dispatcher, static assets."""

from __future__ import annotations

import html as _html
import inspect
import json
import re
import time
import uuid

from pyweb.db import in_scope as _in_scope
from pyweb.rules import ValidationError


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


_RENDER_POOL = None


def _render_pool():
    """Threads for page renders: as many as the server accepts connections."""
    global _RENDER_POOL
    if _RENDER_POOL is None:
        import concurrent.futures as _fut
        from pyweb.config import settings
        _RENDER_POOL = _fut.ThreadPoolExecutor(max_workers=settings().max_connections,
                                               thread_name_prefix="pyweb-render")
    return _RENDER_POOL


def _rpc_pool():
    global _POOL
    if _POOL is None:
        import concurrent.futures as _fut
        _POOL = _fut.ThreadPoolExecutor(max_workers=32, thread_name_prefix="pyweb-rpc")
    return _POOL


def _trusted_proxies():
    """How many reverse proxies to trust for X-Forwarded-For (``PYWEB_TRUST_PROXY``)."""
    from pyweb.config import settings
    try:
        return settings().trust_proxy
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


#: RPC bodies nested deeper, or holding more values, than this are refused (400).
MAX_JSON_DEPTH = 32
MAX_JSON_ITEMS = 10_000


def _bounded_json(raw):
    """Parse an RPC body, refusing absurd nesting before ``json.loads`` can recurse on it."""
    if raw.count(b"[") + raw.count(b"{") <= MAX_JSON_DEPTH and raw.count(b",") <= MAX_JSON_ITEMS:
        return json.loads(raw)   # too few brackets to nest deeply: no need to scan
    depth = top = items = 0
    in_str = esc = False
    for b in raw:
        if in_str:
            if esc:
                esc = False
            elif b == 0x5C:          # backslash
                esc = True
            elif b == 0x22:          # quote
                in_str = False
            continue
        if b == 0x22:
            in_str = True
            items += 1
        elif b in (0x5B, 0x7B):      # [ {
            depth += 1
            items += 1
            top = max(top, depth)
        elif b in (0x5D, 0x7D):      # ] }
            depth -= 1
        elif b == 0x2C:              # , separates values
            items += 1
        if top > MAX_JSON_DEPTH or items > MAX_JSON_ITEMS * 2:
            raise ValueError("request too complex")
    return json.loads(raw)


class _StreamSlots:
    """Open event streams per client address, so one visitor can't hold every thread."""

    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.open: dict = {}

    def take(self, key, limit):
        with self.lock:
            if self.open.get(key, 0) >= limit:
                return False
            self.open[key] = self.open.get(key, 0) + 1
            return True

    def give_back(self, key):
        with self.lock:
            left = self.open.get(key, 1) - 1
            if left > 0:
                self.open[key] = left
            else:
                self.open.pop(key, None)


STREAM_SLOTS = _StreamSlots()


class _Counted:
    """A stream body that returns its slot when it ends, however it ends."""

    def __init__(self, stream, key):
        self.stream, self.key, self._done = stream, key, False

    def _release(self):
        if not self._done:
            self._done = True
            STREAM_SLOTS.give_back(self.key)

    def __iter__(self):
        try:
            yield from self.stream
        finally:
            self._release()

    async def aiter(self):
        try:
            async for chunk in self.stream.aiter():
                yield chunk
        finally:
            self._release()

    def snapshot(self):
        try:
            return self.stream.snapshot()
        finally:
            self._release()

    def close(self):
        try:
            self.stream.close()
        finally:
            self._release()


def _max_streams():
    from pyweb.config import settings
    return settings().max_streams_per_client


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

    def _check_rpc_access(self, req: Request, fn, *, form=False):
        """Auth + CSRF + rate-limit gate. Returns None (allow) or Response."""
        from pyweb import rpc as _rpc
        blocked = self._guard_failure(req, [getattr(fn, "__pyweb_guard__", None)] if getattr(
            fn, "__pyweb_guard__", None) else None, page=False)
        if blocked is not None:
            return blocked
        need_auth = getattr(fn, "__pyweb_auth__", False)
        need = getattr(fn, "__pyweb_permissions__", [])
        csrf_exempt = getattr(fn, "__pyweb_csrf_exempt__", False)
        if need_auth or need:
            secret = self.auth_secret
            if not secret:
                return None  # no secret configured: decorators are advisory
            from pyweb import auth as _auth
            from pyweb import keys as _keys
            from pyweb.context import session as _session
            session = _auth.session_from_request(req, _keys.verify_keys(secret, "session"), _session.max_age)
            if session is not None and not _auth.session_valid(session, absolute_age=_session.absolute_age):
                session = None
            if session is None or "sub" not in session:
                return self._err(_rpc.Code.AUTH, "authentication required")
            if need and not _auth.can(session.get("roles", []), fn):
                return self._err(_rpc.Code.FORBIDDEN,
                                 f"missing permission: {need}")
            if self.csrf_secret and not csrf_exempt and not form:     # forms carry their own token
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
        from .argcheck import ArgError, check_args
        try:
            return check_args(fn, args, _signature(fn))
        except ArgError as exc:
            raise ValueError(str(exc)) from None

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
            if hdr.get("sec-fetch-site") == "cross-site":
                # Browsers say where a request came from; another site's page can't call these.
                return self._err(_rpc.Code.CSRF, "cross-site RPC call rejected",
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
                payload = _bounded_json(req.body or b"{}")
            except json.JSONDecodeError:
                return self._err(_rpc.Code.VALIDATION, "invalid JSON",
                                 trace_id=trace_id, traceparent=traceparent)
            except (ValueError, RecursionError):
                return self._err(_rpc.Code.VALIDATION,
                                 f"request too complex (at most {MAX_JSON_DEPTH} levels and "
                                 f"{MAX_JSON_ITEMS} values)", trace_id=trace_id, traceparent=traceparent)
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
                    result = _in_scope(fn, clean)
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
            except ValidationError as exc:
                return self._err(_rpc.Code.VALIDATION, str(exc), details={"errors": exc.errors},
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
        fut = _rpc_pool().submit(ctx.run, _in_scope, fn, clean)
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
        if req.path.startswith("/__pyweb/form/"):
            if req.method != "POST":
                return Response(405, "forms accept POST only", {"Allow": "POST", "Content-Type": "text/plain"})
            return self.handle_form(req)
        if req.path.startswith("/__pyweb/files/"):
            return self.handle_file(req)
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
        kit = self._auth_kit()
        if kit is not None:
            resp = kit.handle(self, req)
            if resp is not None:
                return resp
        return self.error_page(404, req)

    def _auth_kit(self):
        found = getattr(getattr(self.app, "app", None), "auth", None)
        return found if type(found).__name__ == "AuthKit" else None

    def _guard_failure(self, req, guards, *, page):
        """None if every guard passes; else the response (redirect to sign in, 401 or 403)."""
        if not guards:
            return None
        import time as _time
        from urllib.parse import urlencode
        from pyweb import rpc as _rpc
        from pyweb.context import session
        kit = self._auth_kit()
        payload = session.user()
        user = kit.user() if kit is not None else payload
        if kit is not None and payload is not None and user is None:
            payload = None                            # a closed account
        roles = set((user.roles or []) if kit is not None and user is not None else (payload or {}).get("roles") or [])
        for g in guards:
            if payload is None:
                if page:
                    return Response(303, "", {"Location": "/login?" + urlencode({"next": req.path}),
                                              "Cache-Control": "no-store"})
                return self._err(_rpc.Code.AUTH, "sign in first")
            if g.get("fresh") and _time.time() - payload.get("auth_time", 0) > g["fresh"]:
                if page:
                    return Response(303, "", {"Location": "/login?" + urlencode({"next": req.path, "fresh": "1"}),
                                              "Cache-Control": "no-store"})
                return self._err(_rpc.Code.AUTH, "please sign in again to continue")
            missing = [r for r in g.get("roles") or () if r not in roles]
            if missing:
                if page:
                    return self.error_page(403, req, title="Not allowed",
                                           message="You don't have access to this page.")
                return self._err(_rpc.Code.FORBIDDEN, "you don't have access to this")
        return None

    # ------------------------------------------------------------- forms
    def _page_for(self, path):
        for pat, name in self.routes:
            m = pat.match(path)
            if m:
                return name, m.groupdict()
        return None, None

    def handle_form(self, req: Request):
        """A ``<Form>`` submit: from the browser runtime (JSON answer) or a plain POST."""
        from pyweb import forms as F
        from pyweb import rpc as _rpc
        from pyweb.ssr import to_jsonable
        m = re.match(r"^/__pyweb/form/(\w+)$", req.path.split("?")[0])
        fn = self.rpc_impls.get(m.group(1)) if m else None
        hdr = {k.lower(): v for k, v in (req.headers or {}).items()}
        wants_json = "application/json" in hdr.get("accept", "")

        def fail(status, message, errors=None, fields=None, fid=None):
            if wants_json:
                body = {"ok": False, "error": message, "errors": errors or {}}
                return Response(status, json.dumps(body), {"Content-Type": "application/json",
                                                           "X-Request-Id": req.id, "Cache-Control": "no-store"})
            if fields is not None and fid is not None:
                page = (fields.get("__pw_page") or [""])[-1]
                if page.startswith("/") and not page.startswith("//"):
                    rendered = self._form_page(req, page, fid, fields, errors or {}, message)
                    if rendered is not None:
                        rendered.status = status
                        return rendered
            return self.error_page(status, req, message=message)

        if fn is None:
            return fail(404, "this form's action doesn't exist")
        if hdr.get("sec-fetch-site") == "cross-site":
            return fail(403, "forms can't be sent from other sites")
        origin = hdr.get("origin")
        host = hdr.get("x-forwarded-host") or hdr.get("host")
        if origin and host and origin != "null":
            from urllib.parse import urlparse as _urlparse
            if _urlparse(origin).netloc != host:
                return fail(403, "forms can't be sent from other sites")
        gate = self._check_rpc_access(req, fn, form=True)
        if gate is not None:
            status = gate.status
            try:
                message = json.loads(gate.body)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = "not allowed"
            return fail(status, message)
        from pyweb.config import settings
        limit = settings().max_upload
        if len(req.body or b"") > limit:
            return fail(413, f"the form is too large (at most {F._size_words(limit)})")
        try:
            fields, files = F.parse_body(req)
        except ValueError as exc:
            return fail(400, str(exc))
        fid = (fields.get("__pw_form") or [m.group(1)])[-1]
        if not F.check_csrf(req.cookies.get(F.CSRF_COOKIE), (fields.get("__pw_csrf") or [""])[-1]):
            return fail(403, "This form has expired. Reload the page and try again.", fields=fields, fid=fid)
        key = (fields.get("__pw_key") or [""])[-1]
        once = f"{fn.__name__}:{key}" if key else None
        if once:
            state, outcome = F.ONCE.claim(once)
            waited = 0.0
            while state == "busy" and waited < 10:
                time.sleep(0.05)
                waited += 0.05
                state, outcome = F.ONCE.claim(once)
                if state == "new":
                    break
            if state == "done":
                return self._form_success(req, outcome, wants_json, fields)
            if state == "busy":
                return fail(409, "this form is still being sent")
        try:
            args = F.build_args(fn, fid, fields, files)
            if self.rpc_timeout:
                result = self._call_with_timeout(fn, args)
            else:
                result = _in_scope(fn, args)
            if inspect.isawaitable(result):
                import asyncio
                result = asyncio.run(_awaited(result))
        except ValidationError as exc:
            F.ONCE.forget(once) if once else None
            general = exc.errors.get("__all__", "")
            return fail(422, general or "Please fix the errors below.", errors=exc.errors, fields=fields, fid=fid)
        except TimeoutError:
            F.ONCE.forget(once) if once else None
            return fail(504, "This took too long. Please try again.", fields=fields, fid=fid)
        except _rpc.RPCError as exc:
            F.ONCE.forget(once) if once else None
            return fail(_rpc.status_for(exc.code), str(exc), fields=fields, fid=fid)
        except Exception as exc:  # noqa: BLE001
            F.ONCE.forget(once) if once else None
            if self.logger is not None:
                self.logger.error(f"form {fn.__name__} failed: {exc}", request_id=req.id, rpc=fn.__name__)
            if self.debug:
                raise
            return fail(500, "Something went wrong. Please try again.", fields=fields, fid=fid)
        try:
            data = to_jsonable(result)
        except TypeError:
            data = None
        outcome = {"result": data, "redirect": F.fill_redirect((fields.get("__pw_redirect") or [""])[-1], result)}
        if once:
            F.ONCE.finish(once, outcome)
        return self._form_success(req, outcome, wants_json, fields)

    def _form_success(self, req, outcome, wants_json, fields):
        import secrets as _secrets
        if wants_json:
            body = dict(outcome, ok=True, next_key=_secrets.token_urlsafe(16))
            return Response(200, json.dumps(body), {"Content-Type": "application/json", "X-Request-Id": req.id,
                                                    "Cache-Control": "no-store"})
        target = outcome.get("redirect")
        if not target:
            page = (fields.get("__pw_page") or ["/"])[-1]
            target = page if page.startswith("/") and not page.startswith("//") else "/"
        return Response(303, "", {"Location": target, "X-Request-Id": req.id})

    def _form_page(self, req, page, fid, fields, errors, message):
        """The page the form was on, with what was typed and the errors (no JavaScript)."""
        from pyweb import forms as F
        if self.app is None:
            return None
        name, params = self._page_for(page.split("?")[0])
        if name is None:
            return None
        values = {k: (v if len(v) > 1 else v[-1]) for k, v in fields.items() if not k.startswith("__pw_")}
        for k, v in fields.items():
            if k.startswith("__pw_has_") and k[9:] not in values:
                values[k[9:]] = []
        token = F.FORM_STATE.set({fid: {"values": values, "errors": errors, "error": message}})
        try:
            page_req = Request("GET", page, req.headers, b"", cookies=req.cookies, client=req.client)
            page_req.id = req.id
            return self._render_page(page_req, name, params)
        finally:
            F.FORM_STATE.reset(token)

    def handle_file(self, req: Request):
        """Files saved with the local storage (``PYWEB_STORAGE=file://...``)."""
        from urllib.parse import unquote
        from pyweb import storage as S
        if req.method not in ("GET", "HEAD"):
            return Response(405, "", {"Allow": "GET, HEAD"})
        store = S.storage()
        key = unquote(req.path.split("?")[0][len("/__pyweb/files/"):])
        if not isinstance(store, S.LocalStorage) or not S.valid_key(key) or not store.exists(key):
            return self.error_page(404, req)
        data = store.open(key)
        kind = S.sniff(data)
        inline = kind.startswith(("image/", "video/", "audio/")) or kind in ("application/pdf", "text/plain")
        headers = {"Content-Type": kind if kind != "text/plain" else "text/plain; charset=utf-8",
                   "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400",
                   "Content-Security-Policy": "default-src 'none'; img-src 'self'; media-src 'self'; sandbox",
                   "Content-Disposition": ("inline" if inline else "attachment") + f'; filename="{key.split("/", 1)[1]}"'}
        return Response(200, data if req.method == "GET" else b"", headers)

    def _render_page(self, req, name, params):
        from pyweb.context import BadRequest, NotFound, Redirect
        from urllib.parse import parse_qs, unquote
        blocked = self._guard_failure(req, self.compiled["pages"].get(name, {}).get("guards"), page=True)
        if blocked is not None:
            return blocked
        path, _, qs = req.path.partition("?")
        try:
            result = self._render_with_timeout(name, {k: unquote(v) for k, v in params.items()},
                                               parse_qs(qs, keep_blank_values=True), path)
        except TimeoutError:
            if self.logger is not None:
                self.logger.error(f"page {name} took too long to render", request_id=req.id, page=name)
            return self.error_page(504, req, title="Gateway timeout",
                                   message="The page took too long to load. Please try again.")
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

    def _render_with_timeout(self, name, params, query, path):
        """Render page ``name``; give up waiting after ``PYWEB_RENDER_TIMEOUT`` seconds.

        Like RPC timeouts, Python can't stop the thread: the render finishes in
        the background, but the visitor gets a 504 instead of waiting forever.
        """
        import concurrent.futures as _fut
        import contextvars
        from pyweb.config import settings
        limit = 0 if self.debug else settings().render_timeout
        if not limit:
            return self.app.render(name, params, query=query, path=path)
        ctx = contextvars.copy_context()
        fut = _render_pool().submit(ctx.run, self.app.render, name, params, query=query, path=path)
        try:
            return fut.result(timeout=limit)
        except _fut.TimeoutError as exc:
            raise TimeoutError() from exc

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
        feed = _rt.read_feed(token, _ctx.verify_keys("feed"))
        if feed is None:
            return self._err(403, "invalid or expired feed; reload the page")
        name, start = feed
        spec = (qs.get("live") or [""])[0]
        if spec and name.startswith("pyweb.live:"):
            from pyweb import livedata
            livedata.adopt_spec(spec, _ctx.verify_keys("live"))   # keep re-running it here too
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
        key = client_ip(req)
        if not STREAM_SLOTS.take(key, _max_streams()):
            return self._err(429, "too many open live connections from this address",
                             retry_after=10)
        body = _rt.EventStream(bus, name, last_id)
        if name.startswith("pyweb.live:"):
            from pyweb import livedata
            body = livedata.WatchedStream(body, name[len("pyweb.live:"):])
        body = _Counted(body, key)
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
