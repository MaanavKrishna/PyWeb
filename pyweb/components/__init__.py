"""Pure-Python component API: real HTML, escaped, no markup required."""

from __future__ import annotations

import html as _html
from dataclasses import dataclass, field

VOID = {"input", "img", "br", "hr", "meta", "link", "source", "wbr", "col",
        "base", "area", "embed", "track", "param"}


@dataclass
class Node:
    tag: str
    children: list = field(default_factory=list)
    props: dict = field(default_factory=dict)

    def render(self):
        tag = self.tag.lower()
        attrs = []
        for key, val in self.props.items():
            if val is None or val is False:
                continue
            key = "class" if key == "class_" else key.rstrip("_")
            if key in ("bind", "onclick", "oninput", "onchange"):
                continue
            if val is True:
                attrs.append(key)
            else:
                attrs.append(f'{key}="{_html.escape(str(val), quote=True)}"')
        head = f"<{tag}{' ' if attrs else ''}{' '.join(attrs)}>"
        if tag in VOID:
            return head
        inner = "".join(c.render() if isinstance(c, Node) else _html.escape(str(c)) for c in self.children)
        return f"{head}{inner}</{tag}>"


def el(tag, *children, **props):
    flat = []
    for c in children:
        if isinstance(c, (list, tuple)):
            flat.extend(c)
        else:
            flat.append(c)
    return Node(tag, flat, props)


def Page(*children, title="PyWeb", **props):
    return el("div", *children, **{"data-page": title, **props})


def Heading(text, level=1, **props):
    return el(f"h{level}", text, **props)


def Button(label, **props):
    return el("button", label, **props)


def Input(**props):
    return el("input", **props)


def Form(*children, **props):
    return el("form", *children, **props)


def Article(*children, **props):
    return el("article", *children, **props)
