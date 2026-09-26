"""Pure-Python component API with Track-A parity (bind/onclick/expr).

Page()/Heading()/Button()/... build element trees in .py files; SSR renders
them and the JS emitter hydrates bind=/onclick= via the same codegen.
"""
from __future__ import annotations

import html as _html

from pyweb.compiler import ast as _ast


def _to_attr_value(v) -> _ast.AttrValue:
    if isinstance(v, _ast.Static | _ast.Dyn | _ast.HandlerRef | _ast.BindRef | _ast.CssStatic | _ast.CssDyn):
        return v
    if isinstance(v, _SignalRef):
        return _ast.BindRef(v.name)
    if isinstance(v, _HandlerRef):
        return _ast.HandlerRef(v.name)
    if isinstance(v, _Expr):
        return _ast.Dyn(v.code, 1)
    if isinstance(v, dict):
        # css={...} dict prop
        css = "; ".join(f"{k}: {vv}" for k, vv in v.items())
        return _ast.CssStatic(css)
    if isinstance(v, str) and v.startswith("js:"):
        return _ast.Dyn(v[3:], 1)
    return _ast.Static(str(v))


class _SignalRef:
    def __init__(self, name: str):
        self.name = name


class _HandlerRef:
    def __init__(self, name: str):
        self.name = name


class _Expr:
    def __init__(self, code: str):
        self.code = code


def bind(name: str) -> _SignalRef:
    """Reference a signal for bind={...} / dynamic props."""
    return _SignalRef(name)


def onclick(name: str) -> _HandlerRef:
    """Reference a handler for onclick={...}."""
    return _HandlerRef(name)


def expr(code: str) -> _Expr:
    """Inline a Python expression for a dynamic prop or text."""
    return _Expr(code)


class El:
    tag = "div"

    def __init__(self, *children, **props):
        self.children: list = list(children)
        self.props: dict = props
        self._slot: str = props.pop("slot", "")

    def to_ast(self) -> _ast.Element:
        attrs = []
        for k, v in self.props.items():
            key = k.rstrip("_").replace("_", "-") if k != "css" else "css"
            attrs.append(_ast.Attr(key, _to_attr_value(v)))
        kids = []
        for c in self.children:
            if isinstance(c, El):
                e = c.to_ast()
                if c._slot:
                    e.attrs.append(_ast.Attr("slot", _ast.Static(c._slot)))
                kids.append(e)
            elif isinstance(c, _Expr):
                kids.append(_ast.DynText(c.code, 1))
            elif isinstance(c, _SignalRef):
                kids.append(_ast.DynText(c.name, 1))
            else:
                kids.append(_ast.Text(str(c)))
        # assign hids lazily (pipeline/tests renumber via parser convention)
        el = _ast.Element(tag=self.tag, attrs=attrs, children=kids)
        el.hid = id(el) % 100000
        return el

    def render_ssr(self, values: dict | None = None) -> str:
        from pyweb.compiler.codegen.html import SSR

        class _A:
            signals: set[str] = set()
            handlers: set[str] = set()
            components: dict = {}

        return SSR(_A(), values or {}, {}).render([self.to_ast()])


class Page(El):
    tag = "<>"


class Heading(El):
    tag = "h1"


class SubHeading(El):
    tag = "h2"


class Text(El):
    tag = "p"


class Paragraph(El):
    tag = "p"


class Button(El):
    tag = "button"


class Input(El):
    tag = "input"


class Div(El):
    tag = "div"


class Span(El):
    tag = "span"


class List(El):
    tag = "ul"


class ListItem(El):
    tag = "li"


class Link(El):
    tag = "a"


__all__ = [
    "Page",
    "Heading",
    "SubHeading",
    "Text",
    "Paragraph",
    "Button",
    "Input",
    "Div",
    "Span",
    "List",
    "ListItem",
    "Link",
    "bind",
    "onclick",
    "expr",
    "El",
]
