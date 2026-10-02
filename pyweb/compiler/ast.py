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
