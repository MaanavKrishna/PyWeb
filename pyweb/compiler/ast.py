"""UI AST nodes, source spans, and CompileError for .pyweb files."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union


@dataclass(frozen=True)
class Span:
    file: str = "<input>"
    start_line: int = 1
    start_col: int = 0
    end_line: int = 1
    end_col: int = 0
    snippet: str = ""

    def location(self) -> str:
        return f"{self.file}:{self.start_line}:{self.start_col + 1}"

    def format(self, message: str, hint: str | None = None) -> str:
        lines = [f"{self.location()}: error: {message}"]
        if self.snippet:
            src = self.snippet.splitlines()
            first = src[0] if src else ""
            lines.append(f"  {self.start_line} | {first}")
            caret = " " * (len(f"  {self.start_line} | ") + max(0, self.start_col)) + "^"
            lines.append(caret)
        if hint:
            lines.append(f"  hint: {hint}")
        return "\n".join(lines)


class CompileError(Exception):
    """Every compile error carries file, line, column, snippet, and a hint."""

    def __init__(self, message: str, span: Span | None = None, hint: str | None = None):
        self.message = message
        self.span = span or Span()
        self.hint = hint
        super().__init__(self.span.format(message, hint))

    @property
    def file(self) -> str:
        return self.span.file

    @property
    def line(self) -> int:
        return self.span.start_line

    @property
    def column(self) -> int:
        return self.span.start_col


@dataclass(frozen=True)
class Static:
    text: str


@dataclass(frozen=True)
class Dyn:
    code: str
    line: int = 1


@dataclass(frozen=True)
class HandlerRef:
    name: str


@dataclass(frozen=True)
class BindRef:
    name: str


@dataclass(frozen=True)
class CssStatic:
    css: str


@dataclass(frozen=True)
class CssDyn:
    code: str
    line: int = 1


AttrValue = Union[Static, Dyn, HandlerRef, BindRef, CssStatic, CssDyn]


@dataclass
class Attr:
    name: str
    value: AttrValue
    span: Span = field(default_factory=Span)


@dataclass
class Text:
    content: str
    span: Span = field(default_factory=Span)
    hid: int = 0


@dataclass
class DynText:
    code: str
    line: int = 1
    span: Span = field(default_factory=Span)
    hid: int = 0
    live: bool = False


@dataclass
class Element:
    tag: str
    attrs: list[Attr] = field(default_factory=list)
    children: list[Any] = field(default_factory=list)
    span: Span = field(default_factory=Span)
    hid: int = 0
    live: bool = False
    css_class: str = ""


@dataclass
class For:
    var: str
    iter_code: str
    iter_line: int = 1
    key_code: str | None = None
    body: list[Any] = field(default_factory=list)
    span: Span = field(default_factory=Span)
    hid: int = 0
    live: bool = False


@dataclass
class CondBranch:
    cond_code: str | None
    cond_line: int = 1
    body: list[Any] = field(default_factory=list)
    span: Span = field(default_factory=Span)


@dataclass
class Cond:
    branches: list[CondBranch] = field(default_factory=list)
    span: Span = field(default_factory=Span)
    hid: int = 0
    live: bool = False


@dataclass
class CompUse:
    name: str
    props: dict[str, Attr] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)
    span: Span = field(default_factory=Span)
    hid: int = 0


@dataclass
class Slot:
    name: str = "default"
    span: Span = field(default_factory=Span)
    hid: int = 0


@dataclass
class Style:
    css: str
    span: Span = field(default_factory=Span)


@dataclass
class Root:
    kind: str  # "page" or "component"
    name: str  # component name or "page"
    nodes: list[Any] = field(default_factory=list)
    span: Span = field(default_factory=Span)


@dataclass
class Document:
    filename: str
    python_src: str
    roots: list[Root] = field(default_factory=list)
    styles: list[Style] = field(default_factory=list)
    line_snippets: dict[int, str] = field(default_factory=dict)

    def snippet(self, line: int) -> str:
        return self.line_snippets.get(line, "")


def iter_nodes(nodes: list[Any]):
    for node in nodes:
        yield node
        children: list[Any] = []
        if isinstance(node, (Element, For, CompUse)):
            children = node.body if isinstance(node, For) else (node.children)
        elif isinstance(node, Cond):
            for branch in node.branches:
                children.extend(branch.body)
        for child in iter_nodes(children):
            yield child
