"""Edits to an app's source made by the dev toolbar ("Save to app.pyweb").

Only what the developer asked for changes: the ``theme=`` argument of the ``App(...)`` call
(added when missing), plus ``from pyweb.ui import Theme`` when the file doesn't import it.
Everything else in the file, comments and formatting included, stays as it was.
"""

from __future__ import annotations

import ast

from .theme import Theme

FIELDS = ("accent", "gray", "radius", "font", "density", "dark")


def make_theme(fields: dict) -> Theme:
    """A Theme from the toolbar's fields (strings from a form or JSON; raises ValueError)."""
    clean = {k: fields[k] for k in FIELDS if k in fields and fields[k] not in (None, "")}
    if "radius" in clean:
        r = float(clean["radius"])
        clean["radius"] = int(r) if r == int(r) else r
    return Theme(**clean)


def theme_code(fields: dict) -> str:
    """``Theme(accent="#…", radius=12)``: the values that differ from the defaults (validated)."""
    th = make_theme(fields)
    default = Theme()
    parts = [f"{k}={getattr(th, k)!r}" for k in FIELDS if getattr(th, k) != getattr(default, k)]
    return "Theme(" + ", ".join(parts).replace("'", '"') + ")"


def _offset(lines, lineno, col):
    """Character offset of an ast position (whose column counts UTF-8 bytes)."""
    line = lines[lineno - 1] if lineno - 1 < len(lines) else ""
    return sum(len(x) for x in lines[:lineno - 1]) + len(line.encode("utf-8")[:col].decode("utf-8", "ignore"))


def set_theme(source: str, fields: dict) -> str:
    """``source`` with ``App(..., theme=Theme(...))`` set to ``fields``."""
    from pyweb.compiler.parser import split_sources
    py, _ui = split_sources(source)
    tree = ast.parse(py)
    call = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (getattr(node.func, "id", None) == "App" or getattr(node.func, "attr", None) == "App"):
            call = node
            break
    if call is None:
        raise ValueError("no App(...) call in the app file")
    code = theme_code(fields)
    lines = source.splitlines(keepends=True)
    kw = next((k for k in call.keywords if k.arg == "theme"), None)
    if kw is not None:
        start = _offset(lines, kw.value.lineno, kw.value.col_offset)
        end = _offset(lines, kw.value.end_lineno, kw.value.end_col_offset)
        ext = next((k for k in getattr(kw.value, "keywords", []) if k.arg == "extend"), None)
        if ext is not None:                           # the toolbar doesn't edit extend=: keep it as written
            seg = source[_offset(lines, ext.value.lineno, ext.value.col_offset):
                         _offset(lines, ext.value.end_lineno, ext.value.end_col_offset)]
            code = code[:-1] + (", " if code != "Theme()" else "") + f"extend={seg})"
        out = source[:start] + code + source[end:]
    else:
        end = _offset(lines, call.end_lineno, call.end_col_offset) - 1      # the closing ")"
        has_args = bool(call.args or call.keywords)
        before = source[:end].rstrip()
        sep = "" if not has_args else (" " if before.endswith(",") else ", ")
        out = before + sep + f"theme={code}" + source[end:]
    if not _imports_theme(tree):
        out = _add_import(out, tree)
    return out


def _imports_theme(tree):
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "pyweb.ui" and any(
                a.name in ("Theme", "*") for a in node.names):
            return True
    return False


def _add_import(source, tree):
    lines = source.splitlines(keepends=True)
    last = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last = node.end_lineno
        elif not (isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant)):
            break
    lines.insert(last, "from pyweb.ui import Theme\n")
    return "".join(lines)


def save_theme(path: str, fields: dict) -> str:
    """Rewrite the app file at ``path`` (atomically). Returns the Theme(...) code written."""
    import os
    import tempfile
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    new = set_theme(source, fields)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".pyweb.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return theme_code(fields)
