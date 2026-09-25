"""Parse `.pyweb` source: Python via CPython `ast`, markup via tokenizer.

Markup lines are replaced with `__pyweb_ui__(<lineno>)` placeholders so
`ast` parses real scoping; control lines (`for`/`if` over markup) stay as
Python AND join the UI tree. Content lines (`{expr}` / text inside an open
tag) are detected with tag-depth tracking + an eval/exec parse test.
"""

from __future__ import annotations

import ast
import re

from .ast import ControlFor, ControlIf, Element, ExprNode, TextNode

TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9._-]*)((?:[^{<>\"']|\"[^\"]*\"|'[^']*'|\{[^{}]*\})*)?(\/?)>")
ATTR_RE = re.compile(r"([A-Za-z_@][\w:.-]*)(?:\s*=\s*(\{[^{}]*\}|\"[^\"]*\"|'[^']*'|[^\s>]+))?")
CTL_RE = re.compile(r"^(for\s+.+\s+in\s+.+:|if\s+.+:|elif\s+.+:|else\s*:)\s*(#.*)?$")
VOID = {"input", "img", "br", "hr", "meta", "link", "source", "wbr",
        "col", "base", "area", "embed", "track", "param"}


def _indent(line):
    return len(line) - len(line.lstrip())


def split_sources(source):
    lines = source.splitlines()
    n = len(lines)
    stripped = [l.strip() for l in lines]
    is_tag = [s.startswith("<") for s in stripped]

    def next_sig(i):
        j = i + 1
        while j < n and not stripped[j]:
            j += 1
        return j if j < n else None

    # Control lines governing markup (fixpoint for nesting).
    is_ctl = [False] * n
    changed = True
    while changed:
        changed = False
        for i, s in enumerate(stripped):
            if CTL_RE.match(s):
                j = next_sig(i)
                if j is not None and (is_tag[j] or is_ctl[j]) and not is_ctl[i]:
                    is_ctl[i] = True
                    changed = True

    # Content lines: inside an open tag, expression-like or non-Python.
    is_content = [False] * n
    depth = 0
    for i, (line, s) in enumerate(zip(lines, stripped)):
        if not s:
            continue
        if _indent(line) == 0 and not is_tag[i]:
            depth = 0
        if is_tag[i]:
            for m in TAG_RE.finditer(s):
                closing, tag, _a, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
                if closing:
                    depth = max(0, depth - 1)
                elif not selfclose and tag.lower() not in VOID:
                    depth += 1
            continue
        if is_ctl[i]:
            continue
        if depth > 0:
            try:
                ast.parse(s, mode="eval")
                is_content[i] = True
            except SyntaxError:
                try:
                    tree = ast.parse(s, mode="exec")
                    # `Count: {x}` parses as a bare annotation — inside an
                    # open tag that is markup text, not Python.
                    sole = tree.body[0] if len(tree.body) == 1 else None
                    if isinstance(sole, ast.Expr) and not isinstance(sole.value, ast.Call):
                        is_content[i] = True
                    elif isinstance(sole, ast.AnnAssign) and sole.value is None:
                        is_content[i] = True
                except SyntaxError:
                    is_content[i] = True

    # Placeholders must form a VALID Python block: emit every markup line as
    # a statement at the enclosing *code* indent (function body level), never
    # deeper than the surrounding Python. The real nesting lives in the UI
    # tree (indentation there is markup, not Python scope).
    def code_indent(i):
        for j in range(i - 1, -1, -1):
            s = stripped[j]
            if not s:
                continue
            if not (is_tag[j] or is_content[j] or is_ctl[j]):
                return _indent(lines[j]) + (4 if s.endswith(":") else 0)
        return 0

    py_lines, ui_lines = [], {}
    for i, line in enumerate(lines):
        ln = i + 1
        if is_tag[i] or is_content[i]:
            ui_lines[ln] = line
            py_lines.append(f"{' ' * code_indent(i)}__pyweb_ui__({ln})")
        elif is_ctl[i]:
            ui_lines[ln] = line
            # Markup control blocks must stay valid Python even when nested
            # inside markup indentation: normalize to the code indent and
            # give the block a placeholder body (real children live in UI).
            ci = code_indent(i)
            py_lines.append(f"{' ' * ci}{stripped[i]}")
            py_lines.append(f"{' ' * (ci + 4)}__pyweb_ui__({ln})")
        else:
            py_lines.append(line)
    return "\n".join(py_lines), ui_lines


class _CtlBox:
    def __init__(self, node, indent):
        self.node = node
        self.indent = indent
        self.in_else = False


def _cur_list(stack, root):
    if not stack:
        return root
    top = stack[-1]
    if isinstance(top, _CtlBox):
        if isinstance(top.node, ControlIf) and top.in_else:
            return top.node.orelse
        return top.node.body
    return top.children


def _append_inline(text, lineno, dest):
    for part in re.split(r"(\{[^{}]*\})", text):
        if not part or not part.strip():
            continue
        m = re.fullmatch(r"\{([^{}]*)\}", part.strip())
        if m:
            dest.append(ExprNode(code=m.group(1).strip(), line=lineno))
        else:
            dest.append(TextNode(text=part.strip(), line=lineno))


def _parse_attrs(raw):
    attrs = {}
    for m in ATTR_RE.finditer(raw or ""):
        key, val = m.group(1), m.group(2)
        if val is None:
            attrs[key] = True
        elif val.startswith("{") and val.endswith("}"):
            attrs[key] = ("expr", val[1:-1].strip(), 0)
        else:
            attrs[key] = ("lit", val.strip("\"'"), 0)
    return attrs


def parse_ui_block(lines: dict[int, str]):
    ordered = sorted(lines.items())
    closes: set[str] = set()
    for _ln, code in ordered:
        for m in TAG_RE.finditer(code.strip()):
            if m.group(1):
                closes.add(m.group(2))
    root: list = []
    stack: list = []
    for lineno, raw in ordered:
        code = raw.strip()
        indent = _indent(raw)
        mf = re.match(r"^for\s+(\w+)\s+in\s+(.+):$", code)
        mi = re.match(r"^if\s+(.+):$", code)
        me = re.match(r"^else\s*:$", code)
        ml = re.match(r"^elif\s+(.+):$", code)
        if mf or mi or me or ml:
            if me or ml:
                while stack and isinstance(stack[-1], _CtlBox) and indent < stack[-1].indent:
                    stack.pop()
            else:
                while stack and isinstance(stack[-1], _CtlBox) and indent <= stack[-1].indent:
                    stack.pop()
            if me or ml:
                if not stack or not isinstance(stack[-1], _CtlBox) or not isinstance(stack[-1].node, ControlIf):
                    raise SyntaxError(f"pyweb: stray {code!r} at line {lineno}")
                box = stack[-1]
                if me:
                    box.in_else = True
                    continue
                nested = ControlIf(test=ml.group(1), body=[], line=lineno)
                box.node.orelse.append(nested)
                stack.append(_CtlBox(nested, indent))
                continue
            node = (ControlFor(target=mf.group(1), iterable=mf.group(2).strip(), body=[], line=lineno)
                    if mf else ControlIf(test=mi.group(1).strip(), body=[], line=lineno))
            _cur_list(stack, root).append(node)
            stack.append(_CtlBox(node, indent))
            continue
        if "<" in code and TAG_RE.search(code):
            while stack and isinstance(stack[-1], _CtlBox) and indent <= stack[-1].indent:
                stack.pop()
            pos = 0
            for m in TAG_RE.finditer(code):
                pre = code[pos:m.start()]
                if pre.strip():
                    _append_inline(pre, lineno, _cur_list(stack, root))
                closing, tag, raw_attrs = m.group(1), m.group(2), m.group(3) or ""
                if closing:
                    if not stack or not isinstance(stack[-1], Element) or stack[-1].tag != tag:
                        raise SyntaxError(f"pyweb: mismatched </{tag}> at line {lineno}")
                    stack.pop()
                else:
                    attrs = _parse_attrs(raw_attrs)
                    for k in list(attrs):
                        if isinstance(attrs[k], tuple):
                            _k, body, _l = attrs[k]
                            attrs[k] = (_k, body, lineno)
                    el = Element(tag=tag, attrs=attrs, children=[], line=lineno)
                    _cur_list(stack, root).append(el)
                    if not m.group(4) and tag not in VOID and tag in closes:
                        stack.append(el)
                pos = m.end()
            tail = code[pos:].strip()
            if tail and not tail.startswith("</"):
                _append_inline(tail, lineno, _cur_list(stack, root))
        else:
            _append_inline(code, lineno, _cur_list(stack, root))
    return root


def parse_source(source, filename="<pyweb>"):
    py_source, ui_lines = split_sources(source)
    tree = ast.parse(py_source, filename=filename)
    ui = parse_ui_block(ui_lines) if ui_lines else []
    pages = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            span = (node.lineno, getattr(node, "end_lineno", node.lineno))
            body_ui = [x for x in ui if span[0] <= getattr(x, "line", 0) <= span[1]]
            pages.append((node, body_ui))
    return tree, ui, pages
