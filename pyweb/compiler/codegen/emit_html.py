"""SSR HTML emitter: UI AST → HTML string with binding markers."""

from __future__ import annotations

import ast
import html as htmlmod


def _eval_static(code, initial):
    try:
        return eval(compile(ast.parse(code, mode="eval"), "<pyweb-ssr>", "eval"), {}, dict(initial))
    except Exception:
        return None


def emit_html(ui, initial=None, rpc=None, computeds=None):
    initial = dict(initial or {})
    for name, spec in (computeds or {}).items():
        if name not in initial:
            val = _eval_static(spec["code"] if isinstance(spec, dict) else spec, initial)
            if val is not None:
                initial[name] = val
    parts = []
    for n in ui:
        parts.append(_node(n, initial))
    return "".join(parts)


def _node(n, initial):
    t = type(n).__name__
    if t == "TextNode":
        return htmlmod.escape(n.text)
    if t == "ExprNode":
        val = _eval_static(n.code, initial)
        marker = f"pw-bind=\"{htmlmod.escape(n.code)}\""
        return f"<span {marker}>{htmlmod.escape('' if val is None else str(val))}</span>"
    if t == "ControlFor":
        out = []
        items = _eval_static(n.iterable, initial)
        if isinstance(items, (list, tuple)):
            for item in items:
                scope = dict(initial)
                scope[n.target] = item
                out.append("".join(_node(c, scope) for c in n.body))
        else:
            hid = abs(hash((n.target, n.iterable, n.line))) % 100000
            out.append(f"<!--pw:{hid}-->")
            out.append("".join(_node(c, initial) for c in n.body))
            out.append(f"<!--/pw:{hid}-->")
        return "".join(out)
    if t == "ControlIf":
        val = _eval_static(n.test, initial)
        if val is True:
            return "".join(_node(c, initial) for c in n.body)
        if val is False:
            return "".join(_node(c, initial) for c in n.orelse)
        return "".join(_node(c, initial) for c in n.body)
    if t == "Element":
        tag = n.tag.lower() if not n.is_component else "div"
        VOID_TAGS = {"input", "img", "br", "hr", "meta", "link", "source",
                     "wbr", "col", "base", "area", "embed", "track", "param"}
        attrs = []
        if getattr(n, "pw_id", None):
            attrs.append(f'data-pw-id="{n.pw_id}"')
        for key, val in n.attrs.items():
            if key in ("bind", "onclick", "oninput", "onchange"):
                continue
            if isinstance(val, tuple):
                kind, body, _line = val
                if kind == "lit":
                    attrs.append(f'{key.rstrip("_")}="{htmlmod.escape(body)}"')
                else:
                    static = _eval_static(body, initial)
                    if static is not None:
                        attrs.append(f'{key.rstrip("_")}="{htmlmod.escape(str(static))}"')
                    else:
                        attrs.append(f'data-pw-attr="{htmlmod.escape(body)}"')
            elif val is True:
                attrs.append(key.rstrip("_"))
        inner = "".join(_node(c, initial) for c in n.children)
        open_tag = f"<{tag}{' ' if attrs else ''}{' '.join(attrs)}>"
        if tag in VOID_TAGS:
            return open_tag
        # dynamic text bindings: mark parent for activation
        return f"{open_tag}{inner}</{tag}>"
    if t == "_ControlBox":
        return "".join(_node(c, initial) for c in n.node.body)
    return ""


def emit_page(route, title, ui, initial=None, js_url=None, css="", computeds=None):
    body = emit_html(ui, initial, computeds=computeds)
    js = f'\n<script type="module" src="{js_url}"></script>' if js_url else ""
    return ("<!doctype html><html><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{htmlmod.escape(title)}</title><style>{css}</style></head>"
            f"<body><div id=\"pyweb-root\" data-route=\"{htmlmod.escape(route)}\">{body}</div>{js}</body></html>")


def emit_island(name, ui, initial=None):
    """Render one interactive island: static SSR shell + activation marker."""
    initial = initial or {}
    return (f'<div data-pyweb-island="{htmlmod.escape(name)}">'
            f"{emit_html(ui, initial)}</div>")


def stream_shell(route, title, js_url=None, css=""):
    """Streaming SSR: head + root-open chunks; caller appends fragments."""
    js = f'\n<script type="module" src="{js_url}"></script>' if js_url else ""
    head = ("<!doctype html><html><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{htmlmod.escape(title)}</title><style>{css}</style></head>"
            f"<body><div id=\"pyweb-root\" data-route=\"{htmlmod.escape(route)}\">")
    tail = f"</div>{js}</body></html>"
    return head, tail


def stream_fragment(ui, initial=None):
    return emit_html(ui, initial)
