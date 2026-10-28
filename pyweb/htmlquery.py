"""Find things in HTML in tests: ``response.find("li.done")``, ``rendered.text``.

A small parser over the standard library's ``html.parser``. Selectors: ``tag``, ``#id``,
``.class``, ``[attr]``, ``[attr=value]``, combined (``button.primary[disabled]``) and descendant
(``ul.todos li``), and several separated by commas.
"""

from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Node:
    def __init__(self, tag, attrs, parent=None):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list = []

    @property
    def text(self):
        """The text inside, with whitespace collapsed (what a person reads)."""
        parts = []

        def walk(n):
            for c in n.children:
                if isinstance(c, str):
                    parts.append(c)
                elif c.tag not in ("script", "style", "template"):
                    walk(c)
        walk(self)
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    @property
    def classes(self):
        return (self.attrs.get("class") or "").split()

    def get(self, name, default=None):
        return self.attrs.get(name, default)

    def find(self, selector):
        found = self.find_all(selector)
        return found[0] if found else None

    def find_all(self, selector):
        out = []
        for part in selector.split(","):
            for n in _select(self, part.strip()):
                if n not in out:
                    out.append(n)
        return out

    def __repr__(self):
        return f"<{self.tag} {self.attrs}>"


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {})
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(text):
    p = _Parser()
    p.feed(text if isinstance(text, str) else text.decode("utf-8", "replace"))
    p.close()
    return p.root


_SIMPLE = re.compile(r"([a-zA-Z][\w-]*|\*)?((?:[#.][\w-]+|\[[^\]]+\])*)")


def _matches(node, simple):
    m = _SIMPLE.fullmatch(simple)
    if not m:
        raise ValueError(f"unsupported selector {simple!r}")
    tag, rest = m.group(1), m.group(2)
    if tag and tag != "*" and node.tag != tag.lower():
        return False
    parts = [(t[0], t[1]) if t[0] else ("[", t[2]) for t in re.findall(r"([#.])([\w-]+)|\[([^\]]+)\]", rest)]
    for kind, value in parts:
        if kind == "#" and node.attrs.get("id") != value:
            return False
        if kind == "." and value not in node.classes:
            return False
        if kind == "[":
            name, eq, want = value.partition("=")
            name = name.strip()
            if name not in node.attrs:
                return False
            if eq and node.attrs[name] != want.strip().strip("\"'"):
                return False
    return True


def _descendants(node):
    for c in node.children:
        if isinstance(c, Node):
            yield c
            yield from _descendants(c)


def _select(root, selector):
    steps = selector.split()
    current = [root]
    for step in steps:
        nxt = []
        for base in current:
            for d in _descendants(base):
                if _matches(d, step) and d not in nxt:
                    nxt.append(d)
        current = nxt
    return current


def text_of(markup):
    return parse(markup).text


def escape(s):
    return _html.escape(s)
