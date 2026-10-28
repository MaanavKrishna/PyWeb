"""HTTP routes and middleware: JSON APIs, file downloads, health checks of your own, anything that
isn't a page.

::

    from pyweb import App
    from pyweb.routes import json, csv

    app = App()

    @app.route("/api/items/{id:int}")
    def get_item(id: int):
        item = Item.get(id)
        return item.to_dict() if item else json({"error": "not found"}, status=404)

    @app.route("/api/items", methods=["POST"], login=True)
    def add_item(body: dict):
        return json(Item.create(name=body["name"]).to_dict(), status=201)

    @app.route("/export.csv", roles=["admin"])
    def export():
        return csv(Item.all(), filename="items.csv")

    @app.middleware
    def timing(request, call_next):
        response = call_next(request)
        response.headers["Server-Timing"] = "app"
        return response

A handler gets its path parameters (``{id:int}``, ``{slug}``, ``{rest:path}``; also ``float`` and
``uuid``) by name, query parameters by name (typed by the annotation, like pages), ``body`` (the
JSON body, or a form's fields) and ``request`` if it asks for them. ``pyweb.request`` and
``pyweb.session`` work as in server functions.

What it returns becomes the response: a dict or list is JSON, a string is text (``content_type=``
on the route changes that, e.g. ``"text/html"``), bytes are a download, ``None`` is 204, and
:func:`json`, :func:`text`, :func:`html`, :func:`csv`, :func:`redirect` or a :class:`Response` say
exactly what to send.

Routes are server code only: they never reach the browser. They run before pages (a route and a
page can't share a path), behind the same guards as server functions: ``login=``, ``roles=``,
``fresh=``, ``rate="30/minute"``. Requests that change something (POST, PUT, PATCH, DELETE) and
carry the visitor's cookies must come from your own site; API tokens (``tokens=True``) and
``cors=[...]`` origins are the ways in from elsewhere.

Middleware runs around every request the app answers (pages, routes, server functions, forms;
not static files), in the order it was added. It gets the request and ``call_next`` and returns a
response: change it, replace it, or answer without calling ``call_next`` at all.
"""

from __future__ import annotations

import csv as _csv
import io
import json as _json
import re
import uuid as _uuid

from pyweb.context import Redirect, redirect  # noqa: F401 - re-exported for routes

_CONVERTERS = {
    "str": (r"[^/]+", str),
    "int": (r"-?\d+", int),
    "float": (r"-?\d+(?:\.\d+)?", float),
    "uuid": (r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}", _uuid.UUID),
    "path": (r".+", str),
}
_PARAM = re.compile(r"\{(\w+)(?::(\w+))?\}")
METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
UNSAFE = ("POST", "PUT", "PATCH", "DELETE")


def Response(*args, **kwargs):  # noqa: N802 - it is the response class, built lazily
    """``Response(status, body, headers)``: what a route or middleware may return as is."""
    from pyweb.runtime.server import Response as _Response
    return _Response(*args, **kwargs)


def json(data, status=200, headers=None):
    """A JSON response (dates, Models and other values PyWeb knows are converted)."""
    from pyweb.ssr import to_jsonable
    return Response(status, _json.dumps(to_jsonable(data), default=str),
                    {"Content-Type": "application/json", **(headers or {})})


def text(body, status=200, headers=None, content_type="text/plain; charset=utf-8"):
    return Response(status, str(body), {"Content-Type": content_type, **(headers or {})})


def html(body, status=200, headers=None):
    """HTML you built yourself. Escape anything that came from a visitor (``html.escape``)."""
    return text(body, status, headers, "text/html; charset=utf-8")


def csv(rows, *, filename=None, columns=None, status=200):
    """A CSV download from dicts, Models (their ``to_dict()``) or lists. ``columns`` picks and orders
    the columns (default: the first row's keys). Values starting with ``= + - @`` are prefixed with
    ``'`` so a spreadsheet won't run them as formulas."""
    out = io.StringIO()
    writer = _csv.writer(out)
    rows = list(rows)
    first = _row(rows[0]) if rows else None
    if isinstance(first, dict):
        columns = list(columns or first.keys())
        writer.writerow(columns)
        for r in rows:
            d = _row(r)
            writer.writerow([_cell(d.get(c)) for c in columns])
    else:
        if columns:
            writer.writerow(columns)
        for r in rows:
            writer.writerow([_cell(v) for v in _row(r)])
    headers = {"Content-Type": "text/csv; charset=utf-8"}
    if filename:
        safe = re.sub(r"[^\w.\- ]", "_", str(filename))[:120] or "export.csv"
        headers["Content-Disposition"] = f'attachment; filename="{safe}"'
    return Response(status, out.getvalue(), headers)


def _row(r):
    if hasattr(r, "to_dict"):
        return r.to_dict()
    if isinstance(r, dict):
        return r
    return list(r) if isinstance(r, (list, tuple)) else [r]


def _cell(v):
    if v is None:
        return ""
    s = v if isinstance(v, str) else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") and isinstance(v, str) else s


# ------------------------------------------------------------------ the table

class Route:
    def __init__(self, path, fn, methods, *, login=False, roles=(), fresh=None, rate=None, per="user",
                 tokens=False, cors=None, csrf=True, content_type=None, name=None):
        if not path.startswith("/"):
            raise ValueError(f"route paths start with '/': {path!r}")
        self.path, self.fn, self.name = path, fn, name or fn.__name__
        methods = [m.upper() for m in (methods or ("GET",))]
        for m in methods:
            if m not in METHODS:
                raise ValueError(f"unknown method {m!r} for {path}")
        if "GET" in methods and "HEAD" not in methods:
            methods.append("HEAD")
        self.methods = tuple(methods)
        self.converters = {}
        parts, last = [], 0
        for m in _PARAM.finditer(path):
            parts.append(re.escape(path[last:m.start()]))
            kind = m.group(2) or "str"
            if kind not in _CONVERTERS:
                raise ValueError(f"{{{m.group(1)}:{kind}}} in {path}: use one of {', '.join(_CONVERTERS)}")
            pattern, convert = _CONVERTERS[kind]
            parts.append(f"(?P<{m.group(1)}>{pattern})")
            self.converters[m.group(1)] = convert
            last = m.end()
        parts.append(re.escape(path[last:]))
        self.pattern = re.compile("^" + "".join(parts) + "$")
        self.guard = None
        if login or roles or fresh:
            self.guard = {"login": True, "roles": list(roles or ()), "fresh": fresh}
        self.rate = None
        if rate is not None:
            from pyweb.decorators import parse_rate
            self.rate = parse_rate(rate, per)
        self.tokens, self.csrf, self.content_type = tokens, csrf, content_type
        self.cors = ([cors] if isinstance(cors, str) else list(cors)) if cors else []

    def match(self, path):
        m = self.pattern.match(path)
        if m is None:
            return None
        try:
            return {k: self.converters[k](v) for k, v in m.groupdict().items()}
        except (ValueError, TypeError):
            return None

    def __repr__(self):
        return f"<Route {'/'.join(self.methods)} {self.path} -> {self.name}>"


def find(routes, path, method):
    """``(route, params)`` for ``path``; ``(None, allowed)`` when only the method is wrong; else None."""
    allowed = []
    for r in routes:
        params = r.match(path)
        if params is None:
            continue
        if method in r.methods or (method == "OPTIONS" and r.cors):
            return r, params
        allowed += r.methods
    return (None, sorted(set(allowed))) if allowed else None


# ------------------------------------------------------------------ answering

def _origin_allowed(route, origin):
    return bool(origin) and ("*" in route.cors or origin in route.cors)


def _cors_headers(route, req):
    origin = req.headers.get("origin")
    if not route.cors or not _origin_allowed(route, origin):
        return {}
    out = {"Access-Control-Allow-Origin": "*" if "*" in route.cors else origin, "Vary": "Origin"}
    if "*" not in route.cors:
        out["Access-Control-Allow-Credentials"] = "true"
    return out


def _body(req):
    raw = req.body or b""
    ctype = (req.headers.get("content-type") or "").lower()
    if not raw:
        return None
    if "json" in ctype:
        from pyweb.runtime.server import _bounded_json
        return _bounded_json(raw)
    if "x-www-form-urlencoded" in ctype:
        from urllib.parse import parse_qs
        return {k: v[-1] for k, v in parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}
    return raw


def _arguments(route, req, params):
    import inspect
    from urllib.parse import parse_qs
    sig = inspect.signature(route.fn)
    query = {k: v[-1] for k, v in parse_qs(req.path.partition("?")[2]).items()}
    args = {}
    for name, p in sig.parameters.items():
        if name in params:
            args[name] = params[name]
        elif name == "request":
            args[name] = req
        elif name == "body":
            args[name] = _body(req)
        elif name in query:
            args[name] = _coerce(query[name], p.annotation, name)
        elif p.default is inspect.Parameter.empty and p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL):
            raise _BadRequest(f"missing query parameter {name!r}")
    return args


class _BadRequest(Exception):
    pass


def _coerce(value, annotation, name):
    import inspect
    if annotation is inspect.Parameter.empty or annotation in (str, "str"):
        return value
    kinds = {int: int, "int": int, float: float, "float": float}
    if annotation in (bool, "bool"):
        return value.lower() in ("1", "true", "yes", "on")
    convert = kinds.get(annotation)
    if convert is None:
        return value
    try:
        return convert(value)
    except ValueError:
        raise _BadRequest(f"query parameter {name!r} must be a {convert.__name__}") from None


def respond(server, route, params, req):
    """Run ``route`` for ``req`` (already matched) and return the runtime Response."""
    from pyweb import rpc as _rpc
    from pyweb.runtime.server import Response as _Response
    cors = _cors_headers(route, req)
    if req.method == "OPTIONS":
        if not cors:
            return _Response(403, "", {"Content-Type": "text/plain"})
        cors.update({"Access-Control-Allow-Methods": ", ".join(m for m in route.methods if m != "HEAD"),
                     "Access-Control-Allow-Headers": req.headers.get("access-control-request-headers")
                     or "Content-Type, Authorization", "Access-Control-Max-Age": "600"})
        return _Response(204, "", cors)
    bearer = (req.headers.get("authorization") or "").startswith("Bearer ")
    if route.tokens and bearer:
        req.token_route = route.name             # lets an API token stand in for a session here
    if req.method in UNSAFE and route.csrf and not bearer:
        from pyweb.security import cross_site
        if cross_site(req.headers) and not _origin_allowed(route, req.headers.get("origin")):
            return _err(403, "requests from other sites can't do this", cors)
    if route.guard is not None:
        failure = server._guard_failure(req, [route.guard], page=False)
        if failure is not None:
            failure.headers.update(cors)
            return failure
    from pyweb.context import session
    payload = session.user()
    if payload is not None and payload.get("api"):
        scopes = payload.get("scopes") or []
        if not route.tokens or ("*" not in scopes and route.name not in scopes):
            return _err(403, "this API token can't use this route", cors)
    if route.rate is not None:
        ok, retry = server._own_limit(req, route.fn, route.rate)
        if not ok:
            return _err(429, f"too many requests: try again in {retry}s", {**cors, "Retry-After": str(retry)})
    try:
        args = _arguments(route, req, params)
        result = route.fn(**args)
    except _BadRequest as exc:
        return _err(400, str(exc), cors)
    except _rpc.RPCError as exc:
        return _err(_rpc.status_for(exc.code), str(exc), cors)
    except Exception as exc:  # noqa: BLE001 - answered as a 500 like a page error
        from pyweb.context import NotFound
        if isinstance(exc, NotFound):
            return _err(404, "not found", cors)
        from pyweb.telemetry import report_error
        report_error(exc, "route", route=route.path)
        if server.logger is not None:
            server.logger.error(f"route {route.path} failed: {exc}", request_id=req.id)
        if server.debug:
            raise
        return _err(500, "internal server error", cors)
    resp = to_response(result, route.content_type)
    for k, v in cors.items():
        resp.headers.setdefault(k, v)
    resp.headers.setdefault("Cache-Control", "no-store")
    if req.method == "HEAD":
        resp.body = ""
    return resp


def _err(status, message, extra=None):
    from pyweb.runtime.server import Response as _Response
    return _Response(status, _json.dumps({"error": message}),
                     {"Content-Type": "application/json", "Cache-Control": "no-store", **(extra or {})})


def to_response(result, content_type=None):
    """What a route (or middleware) returned, as the runtime Response."""
    from pyweb.runtime.server import Response as _Response
    if isinstance(result, _Response):
        return result
    if isinstance(result, Redirect):
        return _Response(result.status, "", {"Location": result.url})
    if result is None:
        return _Response(204, "", {})
    if isinstance(result, (bytes, bytearray)):
        return _Response(200, bytes(result), {"Content-Type": content_type or "application/octet-stream"})
    if isinstance(result, str):
        return _Response(200, result, {"Content-Type": content_type or "text/plain; charset=utf-8"})
    return json(result)


# ------------------------------------------------------------------ middleware

def run_middleware(chain, req, last):
    """``chain`` (outermost first) around ``last(req)``."""
    def step(i):
        if i == len(chain):
            return last
        mw, inner = chain[i], step(i + 1)

        def run(request):
            out = mw(request, inner)
            return out if out is None else to_response(out)    # None: nothing here answered (a static file)
        return run
    return step(0)(req)


# ------------------------------------------------------------------ sitemap and robots

ROBOTS_DEFAULT = "User-agent: *\nAllow: /\nDisallow: /__pyweb/\nDisallow: /admin\n"


def robots_txt(app):
    text = getattr(app, "robots", None)
    if text is False:
        return None
    body = ROBOTS_DEFAULT if text in (None, True) else str(text).rstrip("\n") + "\n"
    if getattr(app, "base_url", None) and "sitemap:" not in body.lower():
        body += f"Sitemap: {app.base_url}/sitemap.xml\n"
    return body


def sitemap_xml(app, compiled):
    """Every page without parameters that anyone may see, plus what ``@app.sitemap`` functions add."""
    from xml.sax.saxutils import escape
    base = getattr(app, "base_url", None)
    if not base:
        return None
    urls = []
    for page in compiled.get("pages", {}).values():
        route = page.get("route")
        if not route or "{" in route or page.get("error_status") or page.get("guards") \
                or (page.get("head") or {}).get("noindex"):
            continue
        urls.append(route)
    for fn in getattr(app, "sitemap_sources", ()):
        for entry in fn() or ():
            urls.append(entry)
    seen, lines = set(), []
    for u in urls:
        loc = u if u.startswith("http") else base + u
        if loc in seen:
            continue
        seen.add(loc)
        lines.append(f"  <url><loc>{escape(loc)}</loc></url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(lines) + "\n</urlset>\n")
