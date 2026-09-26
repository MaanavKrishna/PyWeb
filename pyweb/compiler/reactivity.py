"""Reactive analysis: which expressions read signals / computed values."""
from __future__ import annotations

import ast as pyast
import re

_NAME_RE = re.compile(r"[A-Za-z_]\w*")


def names_in(code: str) -> set[str]:
    try:
        tree = pyast.parse(code, mode="eval")
    except SyntaxError:
        try:
            tree = pyast.parse(code)
        except SyntaxError:
            return set(_NAME_RE.findall(code))
    return {n.id for n in pyast.walk(tree) if isinstance(n, pyast.Name)}


def is_probably_reactive(code: str, signals: set[str]) -> bool:
    return bool(names_in(code) & signals)


def validate_expr(code: str, filename: str, line: int, what: str) -> None:
    from pyweb.compiler.ast import CompileError, Span

    try:
        pyast.parse(code, mode="eval")
    except SyntaxError:
        try:
            pyast.parse(code)
        except SyntaxError as e:
            raise CompileError(
                f"invalid expression in {what}: {e.msg}",
                Span(
                    file=filename,
                    start_line=line,
                    start_col=(e.offset or 1) - 1,
                    end_line=line,
                    end_col=e.offset or 1,
                    snippet="",
                ),
                hint="Check the expression syntax (balanced brackets/quotes).",
            )
