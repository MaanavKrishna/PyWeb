"""Unified AST: Python logic (real `ast` nodes) + UI AST (markup nodes)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TextNode:
    text: str
    line: int = 0


@dataclass
class ExprNode:
    code: str
    line: int = 0


@dataclass
class Element:
    tag: str
    attrs: dict
    children: list
    line: int = 0

    @property
    def is_component(self):
        return bool(self.tag[:1].isupper())


@dataclass
class ControlFor:
    target: str
    iterable: str
    body: list
    line: int = 0


@dataclass
class ControlIf:
    test: str
    body: list
    orelse: list = field(default_factory=list)
    line: int = 0


@dataclass
class SlotNode:
    """Where a layout puts the page (``{children}`` in an ``@app.layout``)."""
    layout: str
    line: int = 0


@dataclass
class MarkdownNode:
    """``<Markdown text={...} />`` (from ``pyweb``): Markdown rendered to safe HTML."""
    attrs: dict
    line: int = 0
