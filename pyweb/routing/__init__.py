"""Routing: typed route patterns, router, redirects, metadata, sitemap."""

from __future__ import annotations

import re

_TYPES = {
    "int": (r"[+-]?\d+", int),
    "float": (r"[+-]?(?:\d+\.\d*|\.\d+|\d+)", float),
    "str": (r"[^/]+", str),
    "path": (r".+", str),
}

_PARAM_RE = re.compile(r"\{(\w+)(?::(\w+))?\}")


class Route:
    """A route like `/users/{user_id:int}` with typed params."""

    def __init__(self, pattern, *, name=None, render="server", meta=None):
        self.pattern = pattern
        self.name = name
        self.render = render
        self.meta = meta or {}
        self.params: list[tuple[str, str]] = []
        body = "^"
        pos = 0
        for m in _PARAM_RE.finditer(pattern):
            body += re.escape(pattern[pos:m.start()])
            pname, ptype = m.group(1), m.group(2) or "str"
            if ptype not in _TYPES:
                raise ValueError(f"unknown route param type {ptype!r} in {pattern!r}")
            self.params.append((pname, ptype))
            body += f"(?P<{pname}>{_TYPES[ptype][0]})"
            pos = m.end()
        body += re.escape(pattern[pos:]) + "$"
        self.regex = re.compile(body)

    def match(self, path):
        m = self.regex.match(path)
        if not m:
            return None
        out = {}
        for pname, ptype in self.params:
            raw = m.group(pname)
            try:
                out[pname] = _TYPES[ptype][1](raw)
            except (ValueError, TypeError):
                return None
        return out

    def url(self, **params):
        def repl(m):
            pname = m.group(1)
            if pname not in params:
                raise KeyError(f"missing route param {pname!r}")
            return str(params[pname])
        return _PARAM_RE.sub(repl, self.pattern)


class Router:
    def __init__(self):
        self.routes: list[Route] = []
        self.redirects: list[tuple[re.Pattern, str]] = []
        self.middlewares: list = []

    def add(self, pattern, **kwargs):
        route = Route(pattern, **kwargs)
        self.routes.append(route)
        return route

    def redirect(self, src, dst):
        self.redirects.append((re.compile("^" + src + "$"), dst))

    def use(self, middleware):
        self.middlewares.append(middleware)

    def resolve(self, path):
        for pat, dst in self.redirects:
            if pat.match(path):
                return {"redirect": dst}
        for route in self.routes:
            params = route.match(path)
            if params is not None:
                return {"route": route, "params": params}
        return None

    def sitemap(self, base=""):
        return [base + r.pattern for r in self.routes]
