"""Parse `.pyweb` source: Python via CPython `ast`, markup via a scanner.

A `.pyweb` file is Python plus *markup statements*. A markup statement is
a line that starts with a tag (``<main>``), a content line inside an open
tag (``Count: {count}``), or a ``for``/``if``/``elif``/``else`` line that
governs markup.

Parsing happens in two passes:

1. :func:`split_sources` classifies every physical line. Markup lines are
   replaced by ``__pyweb_ui__(<lineno>)`` placeholder statements so CPython
   parses the remaining logic exactly (scoping, types, errors). The output
   has **the same number of lines as the input**, so every line number the
   Python parser reports is a real `.pyweb` line. A tag or ``{expression}``
   may span several physical lines; the continuation lines become blank.
2. :func:`parse_ui_block` turns the markup lines into a UI tree using a
   character scanner that understands balanced braces and Python string
   literals inside ``{...}`` holes.
"""

from __future__ import annotations

import ast
import re

from .ast import ControlFor, ControlIf, Element, ExprNode, TextNode

VOID = {"input", "img", "br", "hr", "meta", "link", "source", "wbr",
        "col", "base", "area", "embed", "track", "param"}

CTL_RE = re.compile(r"^(for\s+.+\s+in\s+.+:|if\s+.+:|elif\s+.+:|else\s*:)\s*(#.*)?$")
_TAG_START = re.compile(r"<(?:/?[A-Za-z]|!--)")
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9._:-]*")
_ATTR_NAME = re.compile(r"[A-Za-z_@:][\w:.@-]*")


class PyWebSyntaxError(SyntaxError):
    """A markup error, reported with the `.pyweb` line number."""

    def __init__(self, msg, lineno=None, filename=None):
        super().__init__(f"pyweb: {msg}")
        self.lineno = lineno
        self.filename = filename
        self.pyweb_msg = msg


class _Incomplete(Exception):
    """The scanned text ends inside a tag, expression or string."""


# ---------------------------------------------------------------- scanner

def _skip_string(s, i):
    """``s[i]`` is a quote. Return the index just past the string literal."""
    q = s[i]
    if s.startswith(q * 3, i):
        end = s.find(q * 3, i + 3)
        if end < 0:
            raise _Incomplete
        return end + 3
    j = i + 1
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == q:
            return j + 1
        j += 1
    raise _Incomplete


def _scan_braced(s, i):
    """``s[i] == '{'``. Return ``(inner_code, index_after_closing_brace)``.

    Nested ``()[]{}`` and Python string literals (including f-strings and
    triple quotes) are skipped, so ``{len({"a": 1})}`` is one expression.
    """
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c in "\"'":
            j = _skip_string(s, j)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                if c != "}":
                    raise PyWebSyntaxError(f"unbalanced {c!r} in expression")
                return s[i + 1:j], j + 1
        j += 1
    raise _Incomplete


def _scan_tag(s, i):
    """``s[i] == '<'`` and a tag starts here. Return ``(token, next_index)``."""
    if s.startswith("<!--", i):
        end = s.find("-->", i + 4)
        if end < 0:
            raise _Incomplete
        return ("comment", s[i + 4:end]), end + 3
    j = i + 1
    closing = s.startswith("/", j)
    if closing:
        j += 1
    m = _NAME.match(s, j)
    if not m:
        raise PyWebSyntaxError(f"invalid tag near {s[i:i + 12]!r}")
    tag = m.group(0)
    j = m.end()
    attrs = {}
    order = []
    while True:
        while j < len(s) and s[j].isspace():
            j += 1
        if j >= len(s):
            raise _Incomplete
        if s.startswith("/>", j):
            if closing:
                raise PyWebSyntaxError(f"malformed closing tag </{tag}/>")
            return ("open", tag, attrs, True), j + 2
        if s[j] == ">":
            if closing:
                return ("close", tag), j + 1
            return ("open", tag, attrs, False), j + 1
        if closing:
            raise PyWebSyntaxError(f"unexpected attributes on </{tag}>")
        m = _ATTR_NAME.match(s, j)
        if not m:
            raise PyWebSyntaxError(f"invalid attribute in <{tag}> near {s[j:j + 12]!r}")
        key = m.group(0)
        j = m.end()
        while j < len(s) and s[j].isspace():
            j += 1
        if j < len(s) and s[j] == "=":
            j += 1
            while j < len(s) and s[j].isspace():
                j += 1
            if j >= len(s):
                raise _Incomplete
            c = s[j]
            if c == "{":
                code, j = _scan_braced(s, j)
                val = ("expr", code.strip(), 0)
            elif c in "\"'":
                end = s.find(c, j + 1)
                if end < 0:
                    raise _Incomplete
                val = ("lit", s[j + 1:end], 0)
                j = end + 1
            else:
                k = j
                while k < len(s) and not s[k].isspace() and s[k] != ">" and not s.startswith("/>", k):
                    k += 1
                val = ("lit", s[j:k], 0)
                j = k
        else:
            val = True
        if key in attrs:
            raise PyWebSyntaxError(f"duplicate attribute {key!r} on <{tag}>")
        attrs[key] = val
        order.append(key)


def scan(s):
    """Tokenize one logical markup line.

    Yields ``("text", str)``, ``("expr", code)``, ``("open", tag, attrs,
    self_closing)``, ``("close", tag)`` and ``("comment", str)``. Raises
    :class:`_Incomplete` if the line ends inside a tag or expression.
    """
    out = []
    i = 0
    buf = []
    while i < len(s):
        c = s[i]
        if c == "{":
            if buf:
                out.append(("text", "".join(buf)))
                buf = []
            code, i = _scan_braced(s, i)
            if not code.strip():
                raise PyWebSyntaxError("empty {} expression")
            out.append(("expr", code.strip()))
            continue
        if c == "<" and _TAG_START.match(s, i):
            if buf:
                out.append(("text", "".join(buf)))
                buf = []
            tok, i = _scan_tag(s, i)
            out.append(tok)
            continue
        if c == "}":
            raise PyWebSyntaxError("unmatched '}' in markup (write {'}'} for a literal brace)")
        buf.append(c)
        i += 1
    if buf:
        out.append(("text", "".join(buf)))
    return out


def _complete(s):
    try:
        scan(s)
        return True
    except _Incomplete:
        return False


# ------------------------------------------------------- line classifier

def _indent(line):
    return len(line) - len(line.lstrip())


def _is_tag_line(s):
    return bool(_TAG_START.match(s))


def split_sources(source):
    """Return ``(python_source, ui_lines)``.

    ``python_source`` has exactly as many lines as ``source``.
    ``ui_lines`` maps the first line number of every logical markup line to
    its text (continuations joined with a space, original indent kept).
    """
    lines = source.splitlines()
    n = len(lines)
    stripped = [l.strip() for l in lines]
    kind = ["py"] * n          # py | tag | content | ctl | cont | blank
    logical = {}               # head line index -> joined text

    def next_sig(i):
        j = i + 1
        while j < n and not stripped[j]:
            j += 1
        return j if j < n else None

    def leads_to_markup(i):
        j = next_sig(i)
        if j is None or _indent(lines[j]) <= _indent(lines[i]):
            return False
        if _is_tag_line(stripped[j]):
            return True
        if CTL_RE.match(stripped[j]):
            return leads_to_markup(j)
        return False

    def join_from(i):
        """Join physical lines starting at ``i`` until the markup is complete."""
        text = stripped[i]
        j = i
        while not _complete(text):
            if j + 1 >= n:
                raise PyWebSyntaxError("unterminated tag or expression", lineno=i + 1)
            j += 1
            text = text + " " + stripped[j]
        for k in range(i + 1, j + 1):
            kind[k] = "cont"
        logical[i] = " " * _indent(lines[i]) + text
        return j

    depth = 0
    open_ctl_indents = []      # indents of markup ctl lines still open
    i = 0
    while i < n:
        s = stripped[i]
        if not s:
            kind[i] = "blank"
            i += 1
            continue
        ind = _indent(lines[i])
        if ind == 0 and not _is_tag_line(s):
            depth = 0
            open_ctl_indents = []
        while open_ctl_indents and ind <= open_ctl_indents[-1] and not (
                CTL_RE.match(s) and s.split()[0].rstrip(":") in ("elif", "else")
                and ind == open_ctl_indents[-1]):
            open_ctl_indents.pop()
        if _is_tag_line(s):
            kind[i] = "tag"
            last = join_from(i)
            try:
                toks = scan(logical[i].strip())
            except PyWebSyntaxError as exc:
                raise PyWebSyntaxError(exc.pyweb_msg, lineno=i + 1) from None
            for tok in toks:
                if tok[0] == "open" and not tok[3] and tok[1].lower() not in VOID:
                    depth += 1
                elif tok[0] == "close":
                    depth = max(0, depth - 1)
            i = last + 1
            continue
        if CTL_RE.match(s) and (depth > 0 or open_ctl_indents or leads_to_markup(i)):
            kind[i] = "ctl"
            open_ctl_indents.append(ind)
            i += 1
            continue
        if depth > 0 or open_ctl_indents:
            if _is_content(s):
                kind[i] = "content"
                last = join_from(i) if "{" in s else i
                if last == i:
                    logical[i] = lines[i]
                i = last + 1
                continue
        i += 1

    # Placeholders go at the indent of the enclosing Python block: the
    # indent of the first line of each contiguous markup run, clamped to
    # an indentation level Python will accept there.
    def block_indent(i):
        m = _indent(lines[i])
        for j in range(i - 1, -1, -1):
            if kind[j] != "py":
                continue
            pj = _indent(lines[j])
            if pj < m:
                return m if stripped[j].endswith(":") else pj
            if pj == m:
                return m
        return 0

    py_lines = []
    ui_lines = {}
    run_indent = None
    for i, line in enumerate(lines):
        k = kind[i]
        if k == "py":
            run_indent = None
            py_lines.append(line)
            continue
        if k in ("blank", "cont"):
            py_lines.append("")
            continue
        if run_indent is None:
            run_indent = block_indent(i)
        pad = " " * run_indent
        ln = i + 1
        if k == "ctl":
            ui_lines[ln] = line
            s = CTL_RE.match(stripped[i]).group(1)
            head = s.split()[0].rstrip(":")
            if head == "else":
                py_lines.append(f"{pad}__pyweb_ui__({ln})")
            elif head == "elif":
                py_lines.append(f"{pad}if {s[4:].strip()} __pyweb_ui__({ln})")
            else:
                py_lines.append(f"{pad}{s} __pyweb_ui__({ln})")
        else:
            ui_lines[ln] = logical.get(i, line)
            py_lines.append(f"{pad}__pyweb_ui__({ln})")
    return "\n".join(py_lines), ui_lines


def _is_content(s):
    """Inside an open tag: is this line markup content rather than Python?"""
    if s.startswith("{"):
        return True
    try:
        tree = ast.parse(s, mode="exec")
    except SyntaxError:
        return True
    sole = tree.body[0] if len(tree.body) == 1 else None
    if isinstance(sole, ast.Expr):
        # A bare call (`refresh()`) inside markup is still Python; any
        # other bare expression (`Add`, `Hello world`) is text.
        return not isinstance(sole.value, (ast.Call, ast.Await))
    if isinstance(sole, ast.AnnAssign) and sole.value is None:
        return True  # `Count: {x}` parses as an annotation
    return False


# ------------------------------------------------------------ UI tree

class _CtlBox:
    """Stack entry for an open ``for``/``if`` chain.

    ``node`` is the node placed in the tree; ``cur`` is the ``if``/``elif``
    whose branch is being filled (``elif`` nests as ``orelse=[ControlIf]``).
    """

    def __init__(self, node, indent):
        self.node = node
        self.cur = node
        self.indent = indent
        self.in_else = False


def _cur_list(stack, root):
    if not stack:
        return root
    top = stack[-1]
    if isinstance(top, _CtlBox):
        return top.cur.orelse if top.in_else else top.cur.body
    return top.children


def _norm_text(text, first, last):
    t = re.sub(r"\s+", " ", text)
    if first:
        t = t.lstrip()
    if last:
        t = t.rstrip()
    return t


def _check_expr(code, lineno):
    try:
        ast.parse(code.strip(), mode="eval")
    except SyntaxError as exc:
        raise PyWebSyntaxError(f"invalid expression {{{code}}}: {exc.msg}", lineno=lineno) from None


def _append_tokens(toks, lineno, stack, root, closes):
    for idx, tok in enumerate(toks):
        first, last = idx == 0, idx == len(toks) - 1
        kind = tok[0]
        if kind == "text":
            t = _norm_text(tok[1], first, last)
            if t:
                _cur_list(stack, root).append(TextNode(text=t, line=lineno))
        elif kind == "expr":
            _check_expr(tok[1], lineno)
            _cur_list(stack, root).append(ExprNode(code=tok[1], line=lineno))
        elif kind == "comment":
            continue
        elif kind == "close":
            tag = tok[1]
            if not stack or not isinstance(stack[-1], Element) or stack[-1].tag != tag:
                opened = stack[-1].tag if stack and isinstance(stack[-1], Element) else None
                hint = f" (expected </{opened}>)" if opened else ""
                raise PyWebSyntaxError(f"mismatched </{tag}>{hint}", lineno=lineno)
            stack.pop()
        else:
            _, tag, attrs, selfclose = tok
            fixed = {}
            for k, v in attrs.items():
                if isinstance(v, tuple):
                    if v[0] == "expr":
                        _check_expr(v[1], lineno)
                    v = (v[0], v[1], lineno)
                fixed[k] = v
            el = Element(tag=tag, attrs=fixed, children=[], line=lineno)
            _cur_list(stack, root).append(el)
            if not selfclose and tag.lower() not in VOID:
                stack.append(el)


def parse_ui_block(lines: dict[int, str]):
    """Build the UI tree from ``{lineno: logical_markup_line}``."""
    ordered = sorted(lines.items())
    scanned = {}
    closes: set[str] = set()
    for ln, code in ordered:
        s = code.strip()
        if CTL_RE.match(s):
            continue
        try:
            toks = scan(s)
        except _Incomplete:
            raise PyWebSyntaxError("unterminated tag or expression", lineno=ln) from None
        except PyWebSyntaxError as exc:
            raise PyWebSyntaxError(exc.pyweb_msg, lineno=ln) from None
        scanned[ln] = toks
        closes.update(t[1] for t in toks if t[0] == "close")
    root: list = []
    stack: list = []
    for lineno, raw in ordered:
        code = raw.strip()
        indent = _indent(raw)
        if lineno not in scanned:
            code = CTL_RE.match(code).group(1)
            mf = re.match(r"^for\s+(.+?)\s+in\s+(.+):$", code)
            mi = re.match(r"^if\s+(.+):$", code)
            ml = re.match(r"^elif\s+(.+):$", code)
            if ml or code.startswith("else"):
                while stack and isinstance(stack[-1], _CtlBox) and indent < stack[-1].indent:
                    stack.pop()
                box = stack[-1] if stack else None
                if (not isinstance(box, _CtlBox) or not isinstance(box.node, ControlIf)
                        or box.in_else or box.indent != indent):
                    raise PyWebSyntaxError(f"stray {code!r}", lineno=lineno)
                if ml:
                    _check_expr(ml.group(1), lineno)
                    nested = ControlIf(test=ml.group(1).strip(), body=[], line=lineno)
                    box.cur.orelse.append(nested)
                    box.cur = nested
                else:
                    box.in_else = True
                continue
            while stack and isinstance(stack[-1], _CtlBox) and indent <= stack[-1].indent:
                stack.pop()
            if mf:
                _check_expr(mf.group(2), lineno)
                node = ControlFor(target=mf.group(1).strip(), iterable=mf.group(2).strip(),
                                  body=[], line=lineno)
            else:
                _check_expr(mi.group(1), lineno)
                node = ControlIf(test=mi.group(1).strip(), body=[], line=lineno)
            _cur_list(stack, root).append(node)
            stack.append(_CtlBox(node, indent))
            continue
        toks = scanned[lineno]
        if toks and toks[0][0] in ("open", "close", "comment"):
            while stack and isinstance(stack[-1], _CtlBox) and indent <= stack[-1].indent:
                stack.pop()
        _append_tokens(toks, lineno, stack, root, closes)
    for item in stack:
        if isinstance(item, Element):
            raise PyWebSyntaxError(f"<{item.tag}> is never closed", lineno=item.line)
    return root


def parse_source(source, filename="<pyweb>"):
    try:
        py_source, ui_lines = split_sources(source)
        ui = parse_ui_block(ui_lines) if ui_lines else []
    except PyWebSyntaxError as exc:
        exc.filename = filename
        raise
    tree = ast.parse(py_source, filename=filename)
    pages = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            span = (node.lineno, getattr(node, "end_lineno", node.lineno))
            body_ui = [x for x in ui if span[0] <= getattr(x, "line", 0) <= span[1]]
            pages.append((node, body_ui))
    return tree, ui, pages
