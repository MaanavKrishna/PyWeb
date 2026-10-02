"""Server-side rendering: UI tree + Python values → HTML.

The same UI tree the compiler turns into browser JS is rendered here with
real Python semantics, so the first paint is complete, crawlable HTML.
Rendering rules match the browser runtime:

* ``{expr}`` renders ``str(value)``; ``None`` renders nothing.
* text and attribute values are HTML-escaped; ``javascript:`` URLs in
  ``href``/``src``/``action`` are neutralised.
* ``attr={True}`` renders a bare attribute, ``False``/``None`` omit it.
* ``class={{"done": flag}}`` and ``style={{"color": c}}`` accept dicts.
* ``on*`` handlers are dropped; ``bind={x}`` renders ``x`` as the value.
"""

from __future__ import annotations

import ast
import builtins
import html as _html
import json
import re

from .compiler.ast import ControlFor, ControlIf, Element, ExprNode, TextNode

VOID = {"input", "img", "br", "hr", "meta", "link", "source", "wbr",
        "col", "base", "area", "embed", "track", "param"}
URL_ATTRS = {"href", "src", "action", "formaction", "xlink:href"}
BOOL_PROPS = {"checked", "selected", "disabled", "readonly", "required", "multiple", "hidden"}
_BAD_URL = re.compile(r"^\s*(javascript|vbscript|data:text/html)", re.I)


class Markup(str):
    """Pre-rendered HTML (component children). Not escaped again."""


class RenderError(Exception):
    def __init__(self, msg, lineno=None):
        super().__init__(f"line {lineno}: {msg}" if lineno else msg)
        self.lineno = lineno


def safe_url(v):
    s = str(v)
    return "#" if _BAD_URL.match(re.sub(r"[\x00-\x1f]", "", s)) else s


def text_of(v):
    if v is None:
        return ""
    if isinstance(v, Markup):
        return v
    return _html.escape(str(v), quote=False)


def clsx(v):
    if v is None or v is False:
        return ""
    if isinstance(v, dict):
        return " ".join(k for k, on in v.items() if on)
    if isinstance(v, (list, tuple)):
        return " ".join(c for c in (clsx(x) for x in v) if c)
    return str(v)


def _kebab(k):
    return re.sub(r"[A-Z]", lambda m: "-" + m.group(0).lower(), k).replace("_", "-")


def style_of(v):
    if isinstance(v, dict):
        return "; ".join(f"{_kebab(k)}: {val}" for k, val in v.items() if val is not None and val is not False)
    return str(v)


def to_jsonable(v):
    """Convert server values to JSON for the page state payload."""
    import dataclasses
    import datetime
    import decimal
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple, set, frozenset)):
        return [to_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): to_jsonable(x) for k, x in v.items()}
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return to_jsonable(dataclasses.asdict(v))
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    for attr in ("to_dict", "dict", "model_dump"):
        fn = getattr(v, attr, None)
        if callable(fn):
            return to_jsonable(fn())
    if hasattr(v, "__dict__"):
        return {k: to_jsonable(x) for k, x in vars(v).items() if not k.startswith("_")}
    raise TypeError(f"value of type {type(v).__name__} cannot be sent to the browser")


def state_json(state):
    """JSON for ``<script id="pw-state">`` — safe inside HTML."""
    raw = json.dumps(to_jsonable(state), separators=(",", ":"))
    return raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


class Renderer:
    """Render UI nodes.

    ``component_state(name, props) -> (ui_nodes, env)`` is supplied by the
    caller: the compiler uses static evaluation, the server calls the real
    component function.
    """

    def __init__(self, globals_=None, component_state=None, strict=True):
        self.globals = dict(globals_ or {})
        self.globals.setdefault("__builtins__", builtins)
        self.component_state = component_state
        self.strict = strict

    def eval(self, code, env, lineno=None):
        scope = dict(self.globals)
        scope.update(env)
        try:
            return eval(compile(code, "<pyweb-ssr>", "eval"), scope)  # noqa: S307 - app's own code
        except Exception as exc:  # noqa: BLE001
            if self.strict:
                raise RenderError(f"{{{code}}} raised {type(exc).__name__}: {exc}", lineno) from exc
            return None

    def render(self, nodes, env):
        return "".join(self.node(n, env) for n in nodes)

    def node(self, n, env):
        if isinstance(n, TextNode):
            return _html.escape(n.text, quote=False)
        if isinstance(n, ExprNode):
            return text_of(self.eval(n.code, env, n.line))
        if isinstance(n, ControlFor):
            items = self.eval(n.iterable, env, n.line)
            if items is None:
                return ""
            target = ast.parse(n.target, mode="eval").body
            out = []
            for item in items:
                scope = dict(env)
                _bind(target, item, scope)
                out.append(self.render(n.body, scope))
            return "".join(out)
        if isinstance(n, ControlIf):
            if self.eval(n.test, env, n.line):
                return self.render(n.body, env)
            return self.render(n.orelse, env)
        if isinstance(n, Element):
            if n.is_component:
                return self.component(n, env)
            return self.element(n, env)
        return ""

    def attrs_html(self, n, env):
        parts = []
        for key, val in n.attrs.items():
            if key.startswith("on"):
                continue
            if key == "bind":
                if not isinstance(val, tuple):
                    continue
                v = self.eval(val[1], env, val[2])
                typ = n.attrs.get("type")
                typ = typ[1] if isinstance(typ, tuple) else None
                if typ == "checkbox":
                    if v:
                        parts.append(" checked")
                elif typ == "radio":
                    own = n.attrs.get("value")
                    if isinstance(own, tuple) and str(v) == own[1]:
                        parts.append(" checked")
                elif n.tag.lower() not in ("select", "textarea"):
                    parts.append(f' value="{_html.escape("" if v is None else str(v))}"')
                continue
            name = "class" if key in ("class_", "className") else key
            if val is True:
                parts.append(f" {name}")
                continue
            kind, body, line = val
            v = body if kind == "lit" else self.eval(body, env, line)
            if name == "class":
                v = clsx(v)
                if not v:
                    continue
            elif name == "style" and isinstance(v, dict):
                v = style_of(v)
            if v is None or v is False:
                continue
            if v is True:
                parts.append(f" {name}")
                continue
            if name in URL_ATTRS:
                v = safe_url(v)
            parts.append(f' {name}="{_html.escape(str(v))}"')
        return "".join(parts)

    def element(self, n, env):
        tag = n.tag
        attrs = self.attrs_html(n, env)
        if tag.lower() in VOID:
            return f"<{tag}{attrs}>"
        inner = self.render(n.children, env)
        if tag.lower() == "textarea" and "bind" in n.attrs and isinstance(n.attrs["bind"], tuple):
            v = self.eval(n.attrs["bind"][1], env, n.line)
            inner = _html.escape("" if v is None else str(v), quote=False)
        if tag.lower() == "select" and "bind" in n.attrs and isinstance(n.attrs["bind"], tuple):
            v = self.eval(n.attrs["bind"][1], env, n.line)
            inner = re.sub(r'<option value="([^"]*)"',
                           lambda m: m.group(0) + (" selected" if _html.unescape(m.group(1)) == str(v) else ""),
                           inner)
        return f"<{tag}{attrs}>{inner}</{tag}>"

    def component(self, n, env):
        if self.component_state is None:
            raise RenderError(f"unknown component <{n.tag}>", n.line)
        props = {}
        for key, val in n.attrs.items():
            if val is True:
                props[key] = True
            elif val[0] == "lit":
                props[key] = val[1]
            else:
                props[key] = self.eval(val[1], env, val[2])
        if n.children:
            props["children"] = Markup(self.render(n.children, env))
        ui, cenv = self.component_state(n.tag, props)
        return self.render(ui, cenv)


def _bind(target, value, scope):
    if isinstance(target, ast.Name):
        scope[target.id] = value
        return
    if isinstance(target, (ast.Tuple, ast.List)):
        vals = list(value)
        if len(vals) != len(target.elts):
            raise RenderError(f"cannot unpack {len(vals)} values into {len(target.elts)} names")
        for t, v in zip(target.elts, vals):
            _bind(t, v, scope)
        return
    raise RenderError("unsupported loop target")


def page_html(*, name, title, body, state, js_url=None, css_urls=(), head_extra="", lang="en"):
    """The full HTML document for one page."""
    css = "".join(f'<link rel="stylesheet" href="{_html.escape(u)}">' for u in css_urls)
    script = ""
    if js_url:
        script = (f'<script id="pw-state" type="application/json">{state_json(state)}</script>'
                  f'<script type="module" src="{_html.escape(js_url)}"></script>')
    return (f"<!doctype html>\n<html lang=\"{_html.escape(lang)}\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{_html.escape(title)}</title>{css}{head_extra}</head>"
            f"<body><div data-pw-root=\"{_html.escape(name)}\">{body}</div>{script}</body></html>\n")
