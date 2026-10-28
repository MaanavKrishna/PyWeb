"""Find references, rename, and quick fixes for ``.pyweb`` files (used by :mod:`pyweb.lsp`).

A name is found where it's code: Python lines, ``{...}`` in markup, control lines (``if``,
``for``) and component tags (``<Card>``, ``</Card>``). Text between tags, strings and comments
are left alone, and so are ``obj.name`` attributes and ``name=`` keyword arguments.

A name defined inside a function (a page's variable, a handler) is looked for in that function
only. A module-level name (component, ``@server`` function, Model, constant) is also looked for
in the other ``.pyweb`` files of the folder that import it.
"""

from __future__ import annotations

import ast
import io
import keyword
import os
import re
import tokenize

from .compiler import parser as P

_IDENT = re.compile(r"[A-Za-z_]\w*")
_SUGGEST = re.compile(r"did you mean (?:<(\w+)>|'(\w+)')")
_WRONG = re.compile(r"(?:<(\w+)>|'(\w+)')")


def _python_hits(py, word):
    """``(line0, col)`` of ``word`` as a name in the Python part (markup lines are placeholders)."""
    out = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(py).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return out
    depth = 0
    for i, tok in enumerate(tokens):
        if tok.type == tokenize.OP:
            depth += tok.string in "([{"
            depth -= tok.string in ")]}"
        if tok.type != tokenize.NAME or tok.string != word or "__pyweb_ui__(" in tok.line:
            continue                                        # markup lines are read from the source below
        prev = tokens[i - 1].string if i else ""
        nxt = tokens[i + 1].string if i + 1 < len(tokens) else ""
        if prev == ".":
            continue                                        # obj.word: an attribute
        if depth > 0 and nxt == "=" and prev in ("(", ","):
            continue                                        # f(word=1): a keyword argument
        out.append((tok.start[0] - 1, tok.start[1]))
    return out


def _code_spans(line, depth):
    """The parts of a markup line that are code: ``{...}`` contents (``depth`` carries an open
    brace over from the line before). Returns ``([(start, end)], depth)``."""
    spans, start, quote, i = [], 0 if depth else None, None, 0
    while i < len(line):
        c = line[i]
        if depth and quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif depth and c in "'\"":
            quote = c
        elif c == "{":
            if depth == 0:
                start = i + 1
            depth += 1
        elif c == "}" and depth:
            depth -= 1
            if depth == 0:
                spans.append((start, i))
                start = None
        elif depth == 0 and c in "'\"":                    # an attribute value in quotes: text
            end = line.find(c, i + 1)
            i = len(line) if end < 0 else end + 1
            continue
        i += 1
    if depth and start is not None:
        spans.append((start, len(line)))
    return spans, depth


def _names_in(code, word):
    """Offsets of ``word`` as a name in a bit of Python (skips strings and attributes)."""
    out, quote, i = [], None, 0
    while i < len(code):
        c = code[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"":
            quote = c
            i += 1
            continue
        if c == "#":
            break
        m = _IDENT.match(code, i)
        if m and (i == 0 or not (code[i - 1].isalnum() or code[i - 1] == "_")):
            if m.group(0) == word and code[:i].rstrip()[-1:] != "." and not re.match(r"\s*=(?!=)", code[m.end():]):
                out.append(i)
            i = m.end()
            continue
        i += 1
    return out


def occurrences(text, word):
    """Every ``(line0, col)`` where ``word`` is used as a name in ``text``."""
    if not _IDENT.fullmatch(word or "") or keyword.iskeyword(word):
        return []
    py, _ui = P.split_sources(text)
    lines, py_lines = text.splitlines(), py.splitlines()
    hits = set(_python_hits(py, word))
    depth = 0
    for i, line in enumerate(lines):
        if i < len(py_lines) and py_lines[i].strip() and "__pyweb_ui__(" not in py_lines[i]:
            depth = 0
            continue                                        # a Python line: done above
        if not line.strip():
            continue
        stripped = line.strip()
        if P.CTL_RE.match(stripped) and not depth:          # if x: / for a in b: / else:
            hits.update((i, len(line) - len(line.lstrip()) + off) for off in _names_in(stripped, word))
            continue
        for m in re.finditer(rf"</?{re.escape(word)}\b", line):
            if not _inside_braces(line, m.start()):
                hits.add((i, m.start() + (2 if line[m.start() + 1] == "/" else 1)))
        spans, depth = _code_spans(line, depth)
        for a, b in spans:
            hits.update((i, a + off) for off in _names_in(line[a:b], word))
    return sorted(hits)


def _inside_braces(line, pos):
    spans, _ = _code_spans(line[:pos], 0)
    return bool(spans) and spans[-1][1] == pos


def _scope(text, word, line0):
    """``(first_line0, last_line0)`` of the function that defines ``word`` around ``line0``, or None
    for a module-level name."""
    py, _ = P.split_sources(text)
    try:
        tree = ast.parse(py)
    except SyntaxError:
        return None
    best = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if not (node.lineno - 1 <= line0 <= (node.end_lineno or node.lineno) - 1):
            continue
        if _defines(node, word) and (best is None or node.lineno > best.lineno):
            best = node
    if best is None:
        return None
    return best.lineno - 1, (best.end_lineno or best.lineno) - 1


def _defines(fn, word):
    args = fn.args
    names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
    names += [a.arg for a in (args.vararg, args.kwarg) if a is not None]
    if word in names:
        return True
    if isinstance(fn, ast.Lambda):
        return False
    for node in ast.walk(fn):
        if node is fn:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == word:
            return True
        if isinstance(node, ast.Name) and node.id == word and isinstance(node.ctx, ast.Store):
            return True
        if isinstance(node, (ast.Global, ast.Nonlocal)) and word in node.names:
            return False
    return False


def _importers(path, word):
    """Other ``.pyweb`` files next to ``path`` that import ``word`` from it."""
    if not path:
        return []
    folder = os.path.dirname(os.path.abspath(path))
    stem = os.path.splitext(os.path.basename(path))[0]
    pattern = re.compile(rf"^\s*from\s+\.?{re.escape(stem)}\s+import\s+[^\n]*\b{re.escape(word)}\b", re.M)
    out = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for name in names:
        other = os.path.join(folder, name)
        if not name.endswith(".pyweb") or os.path.abspath(other) == os.path.abspath(path):
            continue
        try:
            with open(other, encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            continue
        if pattern.search(src):
            out.append((other, src))
    return out


def _source_of(path, text, word):
    """The file that defines module-level ``word``, when this one imports it from a sibling."""
    if not path:
        return None
    m = re.search(rf"^\s*from\s+\.?(\w+)\s+import\s+[^\n]*\b{re.escape(word)}\b", text, re.M)
    if not m:
        return None
    other = os.path.join(os.path.dirname(os.path.abspath(path)), m.group(1) + ".pyweb")
    if not os.path.isfile(other):
        return None
    with open(other, encoding="utf-8") as fh:
        return other, fh.read()


def references(text, path, line0, col):
    """``[(path, line0, col, length)]`` for the name at the position (``path`` None: this document)."""
    from .lsp import word_at
    word = word_at(text, line0, col)
    if not word or keyword.iskeyword(word):
        return []
    scope = _scope(text, word, line0)
    if scope is not None:
        a, b = scope
        return [(path, ln, c, len(word)) for ln, c in occurrences(text, word) if a <= ln <= b]
    out = [(path, ln, c, len(word)) for ln, c in occurrences(text, word)]
    origin = _source_of(path, text, word)
    files = []
    if origin is not None:                                  # imported here: the defining file and its importers
        files.append(origin)
        files += [f for f in _importers(origin[0], word) if os.path.abspath(f[0]) != os.path.abspath(path or "")]
    else:
        files = _importers(path, word)
    for other, src in files:
        out += [(other, ln, c, len(word)) for ln, c in occurrences(src, word)]
    return out


def rename(text, path, line0, col, new_name):
    """``{path or None: [(line0, col, old_length)]}`` edits, or raises ValueError for a bad name."""
    if not _IDENT.fullmatch(new_name or "") or keyword.iskeyword(new_name):
        raise ValueError(f"{new_name!r} isn't a valid Python name")
    edits: dict = {}
    for file, ln, c, n in references(text, path, line0, col):
        edits.setdefault(file, []).append((ln, c, n))
    return edits


def quick_fixes(text, diagnostics):
    """Code actions for diagnostics that suggest a name (``did you mean 'x'?``)."""
    lines = text.splitlines()
    out = []
    for d in diagnostics:
        msg = d.get("message", "")
        m = _SUGGEST.search(msg)
        if not m:
            continue
        good = m.group(1) or m.group(2)
        wrong = next((w.group(1) or w.group(2) for w in _WRONG.finditer(msg[:m.start()])), None)
        line0 = d.get("range", {}).get("start", {}).get("line", 0)
        if not wrong or wrong == good or not 0 <= line0 < len(lines):
            continue
        edits = [{"range": {"start": {"line": line0, "character": c.start()},
                            "end": {"line": line0, "character": c.end()}}, "newText": good}
                 for c in re.finditer(rf"(?<![\w.]){re.escape(wrong)}\b", lines[line0])]
        if edits:
            out.append({"title": f"Change {wrong!r} to {good!r}", "kind": "quickfix", "diagnostics": [d],
                        "isPreferred": True, "edit": {"changes": {"__uri__": edits}}})
    return out
