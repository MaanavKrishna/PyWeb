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
import functools
import hashlib
import html as _html
from pyweb import _speedups
import os
import posixpath
import re

from .compiler.ast import ControlFor, ControlIf, Element, ExprNode, MarkdownNode, SlotNode, TextNode

VOID = {"input", "img", "br", "hr", "meta", "link", "source", "wbr",
        "col", "base", "area", "embed", "track", "param"}
URL_ATTRS = {"href", "src", "action", "formaction", "xlink:href", "data", "poster", "cite", "background", "ping"}
#: Attribute names a ``{**dict}`` spread may set (anything else, e.g. "x onmouseover=..", is dropped).
_ATTR_NAME = re.compile(r"[A-Za-z_:][-A-Za-z0-9_:.]*\Z")
#: Never from a spread: inline HTML documents and event handlers in any case.
_SPREAD_BLOCKED = {"srcdoc"}
#: HTML boolean attributes: present or absent, so any falsy value (0 from a database too) leaves
#: them out, as the browser runtime does when it sets the property.
BOOLEAN_ATTRS = {"checked", "disabled", "selected", "readonly", "required", "multiple", "hidden", "open",
                 "autofocus", "inert", "novalidate", "formnovalidate", "defer", "async", "muted", "autoplay",
                 "controls", "loop", "playsinline", "reversed", "allowfullscreen", "default", "ismap", "nomodule"}
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


_SCALARS = frozenset({str, int, float, bool, type(None)})


def to_jsonable(v):
    """Convert server values to JSON for the page state payload."""
    t = type(v)
    # Fast path: page state is mostly plain scalars, lists and dicts of them (rows).
    if t in _SCALARS:
        return v
    if t is dict:
        return {k if type(k) is str else str(k): x if type(x) in _SCALARS else to_jsonable(x) for k, x in v.items()}
    if t is list or t is tuple:
        return [x if type(x) in _SCALARS else to_jsonable(x) for x in v]
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
    raw = _speedups.json_dumps(to_jsonable(state))
    return raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


@functools.lru_cache(maxsize=4096)
def _compiled(code):
    """Template expressions repeat on every request; compile each once."""
    return compile(code, "<pyweb-ssr>", "eval")


@functools.lru_cache(maxsize=1024)
def _loop_target(target):
    return ast.parse(target, mode="eval").body


#: Key in a component's render env naming the .pyweb file it came from, so
#: components it uses are looked up in that file (``None``: the app itself).
NS_KEY = "__pyweb_ns__"
#: Stands in for the page inside a rendered layout until :func:`page_html` nests them.
SLOT_MARK = "\x00pyweb-slot\x00"


def link_current(href, path):
    """``aria-current`` for a link to ``href`` on the page at ``path``: "page", "true" (a parent
    section) or None."""
    if not path or not isinstance(href, str) or not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    from urllib.parse import urlsplit
    parts = urlsplit(href)
    if parts.scheme or parts.netloc:
        return None
    target = parts.path
    if not target:
        return None
    if not target.startswith("/"):
        target = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    if target == path:
        return "page"
    if target != "/" and path.startswith(target.rstrip("/") + "/"):
        return "true"
    return None


class Renderer:
    """Render UI nodes.

    ``component_state(name, props, ns) -> (ui_nodes, env)`` is supplied by
    the caller: the compiler uses static evaluation, the server calls the
    real component function. ``ns`` is the file whose markup uses the
    component (see :data:`NS_KEY`).
    """

    def __init__(self, globals_=None, component_state=None, strict=True, path=None, compiled=None):
        self.path = path   # the request path, for marking links to the current page
        self.globals = dict(globals_ or {})
        self.globals.setdefault("__builtins__", builtins)
        self.component_state = component_state
        self.strict = strict
        # Strict renders (the server) run each UI tree as one compiled function (ssr_codegen);
        # non-strict ones (the compiler's static prerender) interpret, swallowing errors.
        self.compiled = strict if compiled is None else compiled
        self._helpers = None

    def eval(self, code, env, lineno=None):
        scope = dict(self.globals)
        scope.update(env)
        try:
            return eval(_compiled(code), scope)  # noqa: S307 - app's own code
        except Exception as exc:  # noqa: BLE001
            if self.strict:
                raise RenderError(f"{{{code}}} raised {type(exc).__name__}: {exc}", lineno) from exc
            return None

    def render(self, nodes, env):
        if self.compiled and nodes:
            from . import ssr_codegen
            scope = dict(self.globals)
            scope.update(env)
            html = ssr_codegen.run(_compiled_ui(nodes), self, scope, self._helpers or self._make_helpers())
            if _VERIFY:
                self._verify(nodes, env, html)
            return html
        return "".join(self.node(n, env) for n in nodes)

    def _verify(self, nodes, env, html):
        """``PYWEB_SSR_VERIFY=1``: the interpreter must produce exactly the same HTML."""
        self.compiled = False
        try:
            expected = "".join(self.node(n, env) for n in nodes)
        finally:
            self.compiled = True
        if expected != html:
            at = next((i for i, (a, b) in enumerate(zip(expected, html)) if a != b), min(len(expected), len(html)))
            raise AssertionError(f"compiled SSR differs at {at}:\n interpreted: {expected[max(0, at - 60):at + 60]!r}"
                                 f"\n compiled:    {html[max(0, at - 60):at + 60]!r}")

    def _make_helpers(self):
        path = self.path

        def href(name, v):
            v = safe_url(v) if v is not None and v is not False and v is not True else v
            out = _attr(name, v)
            if out and v is not True:
                current = link_current(v, path)
                if current:
                    out = f' aria-current="{current}"' + out
            return out
        self._helpers = (text_of, _attr, href, _bind_attr, _plain, _select_bound, Markup, spread_attrs)
        return self._helpers

    def component_html(self, tag, props, ns):
        """A component's HTML, for compiled templates (props already evaluated)."""
        if self.component_state is None:
            raise RenderError(f"unknown component <{tag}>")
        ui, cenv = self.component_state(tag, props, ns)
        return self.render(ui, cenv)

    def node(self, n, env):
        if isinstance(n, TextNode):
            return _html.escape(n.text, quote=False)
        if isinstance(n, ExprNode):
            return text_of(self.eval(n.code, env, n.line))
        if isinstance(n, MarkdownNode):
            from .markdown import render as markdown
            kind, code, line = n.attrs["text"]
            text = code if kind == "lit" else self.eval(code, env, line)
            attrs = {k: v for k, v in n.attrs.items() if k != "text"}
            user = attrs.pop("class", None) or attrs.pop("class_", None)
            if user is None:
                attrs["class"] = ("lit", "markdown", n.line)
            elif user is True or user[0] == "lit":
                attrs["class"] = ("lit", "markdown" + ("" if user is True else " " + user[1]), n.line)
            else:
                attrs["class"] = ("expr", f"['markdown', ({user[1]})]", user[2])
            return f"<div{self.attrs_html(Element('div', attrs, [], n.line), env)}>{markdown(text)}</div>"
        if isinstance(n, SlotNode):
            return f'<div data-pw-slot="{_html.escape(n.layout)}" style="display:contents">{SLOT_MARK}</div>'
        if isinstance(n, ControlFor):
            items = self.eval(n.iterable, env, n.line)
            if items is None:
                return ""
            target = _loop_target(n.target)
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
            if "data-pw-boundary" in n.attrs:
                return self.boundary(n, env)
            return self.element(n, env)
        return ""

    def boundary(self, n, env):
        """``<ErrorBoundary>``: its children, or its fallback if rendering them raised."""
        from .context import BadRequest, NotFound
        try:
            return self.element(n, env)
        except Exception as exc:  # noqa: BLE001 - shown as the fallback, reported like a page error
            if isinstance(exc, (NotFound, BadRequest)) or isinstance(exc.__cause__, (NotFound, BadRequest)):
                raise                               # the page's answer, not a broken part of it
            from .telemetry import report_error
            report_error(exc, "page", boundary=True)
            kind, body, line = n.attrs["data-fallback"]
            text = body if kind == "lit" else self.eval(body, env, line)
            outer = Element("div", {k: v for k, v in n.attrs.items() if k != "data-fallback"}, [], n.line)
            return (f"<div{self.attrs_html(outer, env)} data-pw-failed><div role=\"alert\" "
                    f"class=\"pw-boundary-fallback\"><p>{_html.escape(str(text), quote=False)}</p></div></div>")

    def attrs_html(self, n, env):
        parts = []
        has_spread = any(k.startswith(SPREAD) for k in n.attrs)
        spread_done = False
        for key, val in n.attrs.items():
            if key.startswith(SPREAD):
                if not spread_done:
                    spread_done = True
                    own = _own_class(n.attrs)
                    own_v = None if own is None else (own[1] if own[0] == "lit" else self.eval(own[1], env, own[2]))
                    dicts = [self.eval(v[1], env, v[2]) for k, v in n.attrs.items() if k.startswith(SPREAD)]
                    parts.append(spread_attrs(explicit_names(n.attrs), own_v, dicts))
                continue
            if has_spread and key in ("class", "class_", "className"):
                continue                              # merged with the spread's classes
            if key.startswith("on") or key == "ref":
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
            if v is None or v is False or (name in BOOLEAN_ATTRS and not v and not isinstance(v, str)):
                continue
            if v is True or (name in BOOLEAN_ATTRS and not isinstance(v, str)):
                parts.append(f" {name}")
                continue
            if name in URL_ATTRS:
                v = safe_url(v)
            if name == "href" and n.tag == "a" and "aria-current" not in n.attrs:
                current = link_current(v, self.path)
                if current:
                    parts.append(f' aria-current="{current}"')
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
            if key.startswith(SPREAD):
                for k, v in dict(self.eval(val[1], env, val[2]) or {}).items():
                    if k not in n.attrs:
                        props.setdefault(k, v)
            elif val is True:
                props[key] = True
            elif val[0] == "lit":
                props[key] = val[1]
            else:
                props[key] = self.eval(val[1], env, val[2])
        if n.children:
            props["children"] = Markup(self.render(n.children, env))
        ui, cenv = self.component_state(n.tag, props, env.get(NS_KEY))
        return self.render(ui, cenv)


_UI_CACHE: dict = {}
_VERIFY = os.environ.get("PYWEB_SSR_VERIFY", "") not in ("", "0")


def _compiled_ui(nodes):
    """The compiled renderer for a UI node list (cached by identity; the list is kept alive)."""
    hit = _UI_CACHE.get(id(nodes))
    if hit is not None and hit[0] is nodes:
        return hit[1]
    from . import ssr_codegen
    compiled = ssr_codegen.compile_ui(nodes)
    if len(_UI_CACHE) > 4096:            # dev reloads make new trees; forget old ones
        _UI_CACHE.clear()
    _UI_CACHE[id(nodes)] = (nodes, compiled)
    return compiled


SPREAD = "**"
SKIP_SPREAD = ("children", "ref", "bind")


def explicit_names(attrs):
    """Attribute names written on the element (they win over a ``{**dict}`` spread)."""
    return frozenset("class" if k in ("class_", "className") else k for k in attrs if not k.startswith(SPREAD))


def _own_class(attrs):
    for k in ("class", "class_", "className"):
        v = attrs.get(k)
        if isinstance(v, tuple):
            return v
    return None


def spread_attrs(explicit, own_class, dicts):
    """HTML for ``{**dict}`` spreads: their keys not written on the element, then the merged class.
    Event handlers and ``children`` are skipped (the browser attaches handlers)."""
    merged, classes = {}, [own_class]
    for d in dicts:
        if not d:
            continue
        for key, v in dict(d).items():
            name = "class" if key in ("class_", "className") else str(key)
            lower = name.lower()
            if name == "class":
                classes.append(v)
            elif (name in explicit or name in SKIP_SPREAD or lower.startswith("on") or name.startswith(SPREAD)
                  or lower in _SPREAD_BLOCKED or not _ATTR_NAME.match(name)):
                continue
            else:
                merged[name] = v
    out = _attr("class", classes)
    return out + "".join(_attr(name, v) for name, v in merged.items())


def _attr(name, v):
    """One dynamic attribute, with the same rules as :meth:`Renderer.attrs_html`."""
    if name == "class":
        v = clsx(v)
        if not v:
            return ""
    elif name == "style" and isinstance(v, dict):
        v = style_of(v)
    if v is None or v is False or (name in BOOLEAN_ATTRS and not v and not isinstance(v, str)):
        return ""
    if v is True or (name in BOOLEAN_ATTRS and not isinstance(v, str)):
        return f" {name}"
    if name.lower() in URL_ATTRS:
        v = safe_url(v)
    return f' {name}="{_html.escape(str(v))}"'


def _bind_attr(tag, typ, own, v):
    if typ == "checkbox":
        return " checked" if v else ""
    if typ == "radio":
        return " checked" if own is not None and str(v) == own else ""
    if tag in ("select", "textarea"):
        return ""
    return f' value="{_html.escape("" if v is None else str(v))}"'


def _plain(v):
    return _html.escape("" if v is None else str(v), quote=False)


def _select_bound(inner, v):
    return re.sub(r'<option value="([^"]*)"',
                  lambda m: m.group(0) + (" selected" if _html.unescape(m.group(1)) == str(v) else ""), inner)


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


def head_tags(title, head):
    """``<meta>``/``<link>`` tags for a page's description, social cards and canonical URL."""
    head = head or {}
    esc = _html.escape
    out = []
    description, image, canonical = head.get("description"), head.get("image"), head.get("canonical")
    if description:
        out.append(f'<meta name="description" content="{esc(str(description))}">')
    if canonical:
        out.append(f'<link rel="canonical" href="{esc(safe_url(str(canonical)))}">')
    if head.get("noindex"):
        out.append('<meta name="robots" content="noindex">')
    if description or image or canonical:
        out.append(f'<meta property="og:title" content="{esc(str(title))}">')
        out.append('<meta property="og:type" content="website">')
        if description:
            out.append(f'<meta property="og:description" content="{esc(str(description))}">')
        if canonical:
            out.append(f'<meta property="og:url" content="{esc(safe_url(str(canonical)))}">')
        if image:
            out.append(f'<meta property="og:image" content="{esc(safe_url(str(image)))}">')
        out.append(f'<meta name="twitter:card" content="{"summary_large_image" if image else "summary"}">')
    return "".join(t.replace("<meta ", "<meta data-pw-head ", 1).replace("<link ", "<link data-pw-head ", 1)
                   for t in out)


def page_html(*, name, title, body, state, js_url=None, css_urls=(), head_extra="", lang="en", importmap=None,
              layouts=(), head=None, nav=True, direction="ltr"):
    """The full HTML document for one page.

    ``importmap`` maps npm specifiers to vendored files. ``layouts`` wrap
    the page, outermost first: dicts with ``name``, ``body`` (containing
    :data:`SLOT_MARK`), ``state``, ``js_url`` and ``version``. ``head`` adds
    description/social/canonical tags. ``nav=False`` turns off client-side
    navigation.
    """
    esc = _html.escape
    css = "".join(f'<link rel="stylesheet" href="{esc(u)}">' for u in css_urls)
    inner = f'<div data-pw-root="{esc(name)}">{body}</div>'
    for lay in reversed(list(layouts)):
        lname = esc(lay["name"])
        # Client-side navigation keeps a layout only while its code, server data and markup
        # (current-link marks aside) are unchanged.
        seen = re.sub(r' aria-current="[^"]*"', "", lay["body"])
        print_ = hashlib.sha256(f'{lay.get("version")}|{seen}|{state_json(lay.get("state") or {})}'.encode())
        inner = (f'<div data-pw-root="{lname}" data-pw-layout="{lname}:{print_.hexdigest()[:12]}">'
                 + lay["body"].replace(SLOT_MARK, inner, 1) + "</div>")
    script = ""
    for lay in layouts:
        if lay.get("js_url"):
            script += (f'<script id="pw-state-{esc(lay["name"])}" type="application/json">'
                       f'{state_json(lay.get("state") or {})}</script>'
                       f'<script type="module" src="{esc(lay["js_url"])}"></script>')
    if js_url:
        script += (f'<script id="pw-state" type="application/json">{state_json(state)}</script>'
                   f'<script type="module" src="{esc(js_url)}"></script>')
    if importmap and script:
        from .packages import importmap_json
        head_extra = f'<script type="importmap">{importmap_json(importmap)}</script>' + head_extra
    if not nav:
        head_extra = '<meta name="pw-nav" content="off">' + head_extra
    return (f"<!doctype html>\n<html lang=\"{esc(lang)}\""
            + (' dir="rtl"' if direction == "rtl" else "") + "><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{esc(title)}</title>{head_tags(title, head)}{css}{head_extra}</head>"
            f"<body>{inner}{script}</body></html>\n")
