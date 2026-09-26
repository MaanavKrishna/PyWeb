"""Parser for .pyweb files (Python logic + JSX-like markup).

Strategy: classify each physical line as markup / style-CSS / python-or-text,
replace non-Python lines with ``pass`` placeholders (line-preserving) so CPython
``ast`` can parse the logic, then build the UI tree from indentation.
"""
from __future__ import annotations

import ast as pyast
import re

from pyweb.compiler.ast import (
    Attr,
    BindRef,
    CompUse,
    Cond,
    CondBranch,
    CssDyn,
    CssStatic,
    Document,
    Dyn,
    DynText,
    Element,
    For,
    HandlerRef,
    Root,
    Slot,
    Span,
    Static,
    Style,
    Text,
)

_CODE_STMT_TYPES = (
    pyast.Assign,
    pyast.AnnAssign,
    pyast.AugAssign,
    pyast.Import,
    pyast.ImportFrom,
    pyast.Return,
    pyast.Global,
    pyast.Nonlocal,
    pyast.Assert,
    pyast.Raise,
    pyast.Delete,
    pyast.FunctionDef,
    pyast.AsyncFunctionDef,
    pyast.ClassDef,
    pyast.While,
    pyast.With,
    pyast.AsyncWith,
    pyast.Try,
)

_ATTR_NAME_RE = re.compile(r"^[A-Za-z_@][\w.:@-]*$")
_TAG_NAME_RE = re.compile(r"^[A-Za-z][\w.-]*$")
_FOR_RE = re.compile(r"^for\s+(?P<var>[A-Za-z_]\w*)\s+in\s+(?P<iter>.+):\s*$")
_IF_RE = re.compile(r"^if\s+(?P<cond>.+):\s*$")
_ELIF_RE = re.compile(r"^elif\s+(?P<cond>.+):\s*$")
_ELSE_RE = re.compile(r"^else\s*:\s*$")
_DEF_RE = re.compile(r"^(?:async\s+)?def\s+[A-Za-z_]\w*\s*\(.*\)\s*(?:->\s*.+?)?\s*:\s*(?:#.*)?$")
_ON_NAME_RE = re.compile(r"^[A-Za-z_]\w*$")


class _Line:
    __slots__ = ("no", "indent", "text", "stripped", "kind")

    def __init__(self, no: int, indent: int, text: str, kind: str):
        self.no = no
        self.indent = indent
        self.text = text
        self.stripped = text.strip()
        self.kind = kind  # "PY" | "MARKUP" | "CSS"


class Parser:
    def __init__(self, source: str, filename: str = "<input>"):
        self.source = source
        self.filename = filename
        self.raw = source.splitlines()
        self.snippets = {i + 1: line for i, line in enumerate(self.raw)}
        self._hid = 0
        self._text_lines: set[int] = set()

    # -- helpers ---------------------------------------------------------
    def err(self, message: str, lineno: int, col: int = 0, hint: str | None = None):
        from pyweb.compiler.ast import CompileError

        span = Span(
            file=self.filename,
            start_line=lineno,
            start_col=col,
            end_line=lineno,
            end_col=col + 1,
            snippet=self.snippets.get(lineno, ""),
        )
        return CompileError(message, span, hint)

    def next_hid(self) -> int:
        self._hid += 1
        return self._hid

    def span_for(self, lineno: int, col: int, end_lineno: int | None = None) -> Span:
        return Span(
            file=self.filename,
            start_line=lineno,
            start_col=col,
            end_line=end_lineno or lineno,
            end_col=col + 1,
            snippet=self.snippets.get(lineno, ""),
        )

    # -- phase 1: classification ------------------------------------------
    @staticmethod
    def _is_markup_start(stripped: str) -> bool:
        if stripped.startswith("<>") or stripped.startswith("</"):
            return True
        return (
            stripped.startswith("<")
            and len(stripped) > 1
            and (stripped[1].isalpha() or stripped[1] == "!")
        )

    @staticmethod
    def _scan_depth(text: str) -> int:
        """Net '<' vs '>' depth, ignoring quoted strings and {...} blocks."""
        depth = 0
        i, n = 0, len(text)
        in_str: str | None = None
        brace = 0
        while i < n:
            c = text[i]
            if in_str:
                if c == "\\":
                    i += 2
                    continue
                if c == in_str:
                    in_str = None
            elif brace > 0:
                if c in "\"'":
                    in_str = c
                elif c == "{":
                    brace += 1
                elif c == "}":
                    brace -= 1
            elif c in "\"'":
                in_str = c
            elif c == "{":
                brace += 1
            elif c == "<":
                nxt = text[i + 1] if i + 1 < n else ""
                if nxt.isalpha() or nxt in "/!>":
                    depth += 1
            elif c == ">":
                depth -= 1
            i += 1
        return depth

    def _indent_of(self, lineno: int, line: str) -> int:
        n = 0
        for ch in line:
            if ch == " ":
                n += 1
            elif ch == "\t":
                raise self.err(
                    "tabs are not allowed for indentation (use spaces)",
                    lineno,
                    0,
                    hint="Replace tab characters with spaces.",
                )
            else:
                break
        return n

    def classify(self) -> list[_Line]:
        lines: list[_Line] = []
        tag_depth = 0
        in_style = False
        for idx, raw in enumerate(self.raw):
            no = idx + 1
            if not raw.strip():
                continue
            indent = self._indent_of(no, raw)
            stripped = raw.strip()
            if stripped.startswith("#") and tag_depth == 0 and not in_style:
                continue
            if in_style:
                if stripped.startswith("</style"):
                    in_style = False
                    lines.append(_Line(no, indent, raw, "MARKUP"))
                else:
                    lines.append(_Line(no, indent, raw, "CSS"))
                continue
            if tag_depth > 0:
                tag_depth += self._scan_depth(stripped)
                lines.append(_Line(no, indent, raw, "MARKUP"))
                if tag_depth <= 0:
                    tag_depth = 0
                continue
            if self._is_markup_start(stripped):
                tag_depth = self._scan_depth(stripped)
                if tag_depth < 0:
                    tag_depth = 0
                lines.append(_Line(no, indent, raw, "MARKUP"))
                low = stripped.lower()
                if low.startswith("<style") and not low.startswith("</style"):
                    if not stripped.rstrip().endswith("/>") and "</style>" not in low:
                        in_style = True
                continue
            lines.append(_Line(no, indent, raw, "PY"))
        if tag_depth > 0:
            last = lines[-1] if lines else None
            raise self.err(
                "unclosed tag: '>' missing",
                last.no if last else 1,
                hint="Close the tag on this line or a following line.",
            )
        if in_style:
            raise self.err(
                "unclosed <style> block: missing </style>",
                len(self.raw),
                hint="Add a </style> line at the same indent as <style>.",
            )
        return lines

    # -- phase 2: root discovery -------------------------------------------
    def _find_roots(self, lines: list[_Line]):
        """Find @page/@component decorated defs: (kind, name, deco_line, def_line, body_indent)."""
        roots = []
        by_no = {ln.no: ln for ln in lines}
        order = sorted(by_no)
        i = 0
        while i < len(order):
            ln = by_no[order[i]]
            if ln.indent == 0 and ln.kind == "PY" and ln.stripped.startswith("@page"):
                target = self._expect_def(order, by_no, i, "@page")
                roots.append(("page", target[0], ln.no, target[1]))
            elif ln.indent == 0 and ln.kind == "PY" and ln.stripped.startswith("@component"):
                target = self._expect_def(order, by_no, i, "@component")
                roots.append(("component", target[0], ln.no, target[1]))
            i += 1
        return roots

    def _expect_def(self, order, by_no, i, deco: str):
        if i + 1 >= len(order):
            raise self.err(
                f"{deco} must decorate a 'def' block",
                by_no[order[i]].no,
                hint=f"Put '{deco}(...)' directly above a 'def name(...):' line.",
            )
        nxt = by_no[order[i + 1]]
        m = re.match(r"^(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", nxt.stripped)
        if nxt.indent != 0 or not m:
            raise self.err(
                f"{deco} must decorate a top-level 'def' block",
                nxt.no,
                hint=f"Put '{deco}(...)' directly above a top-level 'def name(...):' line.",
            )
        return m.group(1), nxt.no

    # -- phase 3: tree building --------------------------------------------
    def parse(self) -> Document:
        lines = self.classify()
        roots_info = self._find_roots(lines)
        owned: set[int] = set()
        roots: list[Root] = []
        styles: list[Style] = []
        for kind, name, deco_no, def_no in roots_info:
            body_indent = self._indent_of(def_no, self.raw[def_no - 1])
            # chunk: lines after def_no up to (not incl.) the next indent-0 line
            cut = []
            for ln in lines:
                if ln.no <= def_no:
                    continue
                if ln.indent == 0:
                    break
                cut.append(ln)
            owned.update(ln.no for ln in cut)
            nodes, _ = self._build_block(cut, 0, body_indent, kind == "component", True)
            span = self.span_for(deco_no, 0, def_no)
            roots.append(Root(kind=kind, name=name, nodes=nodes, span=span))
        # stray top-level markup is an error
        for ln in lines:
            if ln.no not in owned and ln.indent == 0 and ln.kind in ("MARKUP", "CSS"):
                raise self.err(
                    "markup must live inside a @page/@component function body",
                    ln.no,
                    ln.indent,
                    hint="Wrap this markup in '@page(\"/path\")\\ndef name():'.",
                )
        for node in self._walk_all(roots):
            if isinstance(node, Style):
                styles.append(node)
        python_src = self._placeholder_source(lines)
        # validate the placeholder source parses (surfaces logic syntax errors)
        try:
            import ast as pyast

            pyast.parse(python_src, filename=self.filename)
        except SyntaxError as e:
            raise self.err(
                f"invalid Python code: {e.msg}",
                e.lineno or 1,
                (e.offset or 1) - 1,
                hint="Keep Python statements before markup, or check expression syntax.",
            )
        return Document(
            filename=self.filename,
            python_src=python_src,
            roots=roots,
            styles=styles,
            line_snippets=self.snippets,
        )

    def _walk_all(self, roots: list[Root]):
        from pyweb.compiler.ast import iter_nodes

        for root in roots:
            for node in iter_nodes(root.nodes):
                yield node

    def _placeholder_source(self, lines: list[_Line]) -> str:
        out = list(self.raw)
        skip = {ln.no for ln in lines if ln.kind in ("MARKUP", "CSS")} | self._text_lines
        indent_by_no = {ln.no: ln.indent for ln in lines}
        order = sorted(indent_by_no)
        next_indent = {}
        for idx, no in enumerate(order):
            next_indent[no] = indent_by_no[order[idx + 1]] if idx + 1 < len(order) else 0
        for no in skip:
            raw = out[no - 1]
            indent = raw[: len(raw) - len(raw.lstrip())]
            indent = indent.replace("\t", "    ")
            # a replaced line that opens an indented block must stay a block
            # opener ('pass' cannot take a suite, but 'if True:' can)
            if next_indent.get(no, 0) > len(indent):
                out[no - 1] = f"{indent}if True:"
            else:
                out[no - 1] = f"{indent}pass"
        return "\n".join(out) + ("\n" if self.raw else "")

    # -- block builder ------------------------------------------------------
    def _build_block(
        self, lines: list[_Line], pos: int, parent_indent: int, in_component: bool, is_root: bool
    ):
        nodes: list = []
        i = pos
        n = len(lines)
        while i < n:
            ln = lines[i]
            if ln.indent <= parent_indent:
                break
            if ln.kind == "CSS":
                raise self.err(
                    "<style> content must directly follow its <style> line",
                    ln.no,
                    ln.indent,
                    hint="Move this CSS inside a <style> ... </style> block.",
                )
            if ln.kind == "MARKUP":
                if ln.stripped.startswith("</"):
                    break  # close tag belongs to caller
                node, i = self._build_element(lines, i, in_component)
                nodes.append(node)
                continue
            # PY line: control header, nested def, logic, or text
            stripped = ln.stripped
            m_for = _FOR_RE.match(stripped)
            m_if = _IF_RE.match(stripped)
            if m_for or m_if:
                node, i = self._build_control(lines, i, in_component, m_for is not None)
                nodes.append(node)
                continue
            if _ELIF_RE.match(stripped) or _ELSE_RE.match(stripped):
                break  # belongs to an enclosing if
            if re.match(r"^(?:async\s+)?def\s", stripped) or stripped.startswith("class "):
                i = self._skip_block(lines, i, ln.indent, in_component)
                continue
            if re.match(r"^(?:while|try|with|match)\b", stripped):
                if is_root or True:
                    # At root level with pure-python body this is logic; inside
                    # markup it is unsupported.
                    end = self._block_end(lines, i, ln.indent)
                    has_ui = any(
                        l.kind == "MARKUP" or self._looks_like_control(l) for l in lines[i + 1 : end]
                    )
                    if has_ui or not is_root:
                        raise self.err(
                            f"'{stripped.split()[0]}' blocks are not supported inside markup",
                            ln.no,
                            ln.indent,
                            hint="Use 'if'/'for' for UI, or move this logic above the markup.",
                        )
                    i = end
                    continue
            if is_root:
                # logic line: must be (part of) valid Python
                i = self._consume_logic(lines, i)
                continue
            # inside an element: text unless it is clearly a code statement
            if self._is_code_statement(stripped):
                raise self.err(
                    "Python statements must appear before markup, not inside an element",
                    ln.no,
                    ln.indent,
                    hint="Move this statement above the element, or use 'if'/'for' for UI logic.",
                )
            nodes.extend(self._build_text(ln))
            i += 1
        return nodes, i

    def _looks_like_control(self, ln: _Line) -> bool:
        s = ln.stripped
        return bool(_FOR_RE.match(s) or _IF_RE.match(s) or _ELIF_RE.match(s) or _ELSE_RE.match(s))

    def _block_end(self, lines: list[_Line], pos: int, indent: int) -> int:
        j = pos + 1
        while j < len(lines) and lines[j].indent > indent:
            j += 1
        return j

    def _skip_block(self, lines: list[_Line], pos: int, indent: int, in_component: bool) -> int:
        end = self._block_end(lines, pos, indent)
        for l in lines[pos + 1 : end]:
            if l.kind == "MARKUP":
                raise self.err(
                    "markup is not allowed inside nested def/class blocks",
                    l.no,
                    l.indent,
                    hint="Move handler logic to top level and keep markup in the page body.",
                )
        return end

    def _consume_logic(self, lines: list[_Line], pos: int) -> int:
        """Consume 1+ lines forming a Python statement; error if it looks like stray text."""
        ln = lines[pos]
        # gather bracket-continuations
        j = pos
        depth = 0
        while j < len(lines):
            cur = lines[j]
            if j > pos and cur.indent <= lines[pos].indent and depth <= 0:
                break
            depth += cur.stripped.count("(") + cur.stripped.count("[") + cur.stripped.count("{")
            depth -= cur.stripped.count(")") + cur.stripped.count("]") + cur.stripped.count("}")
            if cur.stripped.endswith("\\"):
                j += 1
                continue
            j += 1
            if depth <= 0 and not cur.stripped.endswith(":"):
                # include one indented suite for compound headers
                if cur.stripped.endswith(":"):
                    j = self._block_end(lines, j - 1, cur.indent)
                break
            if depth <= 0:
                break
        import textwrap

        chunk = textwrap.dedent("\n".join(" " * (l.indent) + l.stripped for l in lines[pos:j]))
        try:
            import ast as pyast

            pyast.parse(chunk)
        except SyntaxError:
            raise self.err(
                "unexpected text outside an element",
                ln.no,
                ln.indent,
                hint="Wrap text in an element (e.g. <p>...</p>) or fix the Python syntax.",
            )
        return j

    @staticmethod
    def _is_code_statement(stripped: str) -> bool:
        try:
            import ast as pyast

            mod = pyast.parse(stripped)
        except SyntaxError:
            return False
        if len(mod.body) != 1:
            return False
        return isinstance(mod.body[0], _CODE_STMT_TYPES)

    # -- control flow --------------------------------------------------------
    def _build_control(self, lines: list[_Line], pos: int, in_component: bool, is_for: bool):
        ln = lines[pos]
        if is_for:
            m = _FOR_RE.match(ln.stripped)
            assert m
            body, j = self._build_block(lines, pos + 1, ln.indent, in_component, False)
            if not any(
                not isinstance(b, Text) or b.content.strip() for b in body
            ) and not body:
                raise self.err(
                    "'for' body must contain markup",
                    ln.no,
                    ln.indent,
                    hint="Indent UI elements under the 'for' line.",
                )
            node = For(
                var=m.group("var"),
                iter_code=m.group("iter").strip(),
                iter_line=ln.no,
                body=body,
                span=self.span_for(ln.no, ln.indent),
                hid=self.next_hid(),
            )
            self._extract_key(node)
            return node, j
        # if / elif / else chain
        branches: list[CondBranch] = []
        j = pos
        first = True
        while j < len(lines):
            cur = lines[j]
            if first:
                m = _IF_RE.match(cur.stripped)
                cond = m.group("cond").strip() if m else None
            elif cur.indent == lines[pos].indent and _ELIF_RE.match(cur.stripped):
                m = _ELIF_RE.match(cur.stripped)
                cond = m.group("cond").strip() if m else None
            elif cur.indent == lines[pos].indent and _ELSE_RE.match(cur.stripped):
                cond = None
            else:
                break
            if cur.indent != lines[pos].indent or (first and not m):
                break
            body, j = self._build_block(lines, j + 1, cur.indent, in_component, False)
            branches.append(
                CondBranch(cond_code=cond, cond_line=cur.no, body=body, span=self.span_for(cur.no, cur.indent))
            )
            first = False
            if cond is None:  # else was last
                break
        node = Cond(branches=branches, span=self.span_for(ln.no, ln.indent), hid=self.next_hid())
        return node, j

    def _extract_key(self, node: For) -> None:
        core = [b for b in node.body if not (isinstance(b, Text) and not b.content.strip())]
        if len(core) == 1 and isinstance(core[0], (Element, CompUse)):
            child = core[0]
            attrs = child.attrs if isinstance(child, Element) else child.props
            for k, a in list(attrs.items()) if isinstance(attrs, dict) else [(a.name, a) for a in attrs]:
                attr = a if isinstance(a, Attr) else None
                name = k if isinstance(attrs, dict) else (attr.name if attr else "")
                if name == "key":
                    if isinstance(attrs, dict):
                        attrs.pop(k)
                    else:
                        attrs[:] = [x for x in attrs if x.name != "key"]
                    if isinstance(attr.value, Dyn):
                        node.key_code = attr.value.code
                    elif isinstance(attr.value, Static):
                        node.key_code = repr(attr.value.text)
                    else:
                        raise self.err(
                            "key={...} must be an expression",
                            attr.span.start_line,
                            attr.span.start_col,
                            hint="Use key={item.id} with a unique id expression.",
                        )
                    return
        # check stray key= attrs deeper in the loop body
        for b in node.body:
            targets = b.attrs if isinstance(b, Element) else (b.props.values() if isinstance(b, CompUse) else [])
            for a in targets:
                if a.name == "key":
                    raise self.err(
                        "key= must be on the single root child of a 'for' body",
                        a.span.start_line,
                        a.span.start_col,
                        hint="Put key={...} on the one top-level element inside the loop.",
                    )

    # -- elements -------------------------------------------------------------
    @staticmethod
    def _split_inline(source: str) -> tuple[str, str, str] | None:
        """Split '<tag ...>inner</tag>' on one line. Returns (open, inner, rest)."""
        m = re.match(r"^(<[^<>]*>)(.*)$", source.strip(), re.S)
        if not m:
            return None
        open_tag, rest = m.group(1), m.group(2)
        if open_tag.startswith("</") or open_tag.endswith("/>"):
            return None
        name_m = re.match(r"^<\s*([A-Za-z][\w.-]*)", open_tag)
        if not name_m:
            return None
        name = name_m.group(1)
        close = f"</{name}>"
        if close not in rest:
            return None
        inner, _, after = rest.partition(close)
        if "<" in inner and not re.fullmatch(r"\s*", inner) and re.search(r"<[A-Za-z/<>]", inner):
            # nested tags inline: only allow plain text + {interp} inline
            if re.search(r"</", inner):
                return None
            if re.search(r"<[A-Za-z]", inner):
                return None
        return open_tag, inner, after.strip()

    def _build_element(self, lines: list[_Line], pos: int, in_component: bool):
        ln = lines[pos]
        # gather continuation lines of the same tag (same indent, MARKUP kind)
        j = pos
        parts = [ln.text]
        k = pos + 1
        opens = self._scan_depth(ln.stripped)
        # _scan_depth is net; a complete single tag nets 0. A single-line
        # '<tag>text</tag>' nets 0 too but needs inline splitting below.
        while opens > 0 and k < len(lines) and lines[k].kind == "MARKUP" and lines[k].indent >= ln.indent:
            parts.append(lines[k].text)
            opens += self._scan_depth(lines[k].stripped)
            j = k
            k += 1
        source = "\n".join(parts)
        inline = self._split_inline(source) if j == pos else None
        if inline and not source.strip().startswith("<style"):
            open_tag, inner, after = inline
            if after:
                raise self.err(
                    f"unexpected text after '</{self._tag_name_of(open_tag)}>'",
                    ln.no,
                    ln.indent,
                    hint="Put trailing content on its own line inside the element.",
                )
            tag = self._parse_tag(open_tag, ln)
            if tag["name"] == "slot":
                return self._build_slot(tag, ln, j)
            node = self._make_node(tag, ln, [], in_component)
            if inner.strip():
                fake = _Line(ln.no, ln.indent + 1, " " * (ln.indent + 1) + inner.strip(), "PY")
                for child in self._build_text(fake):
                    if isinstance(node, (Element,)):
                        node.children.append(child)
                    elif isinstance(node, CompUse):
                        node.children.append(child)
            return node, j + 1
        tag = self._parse_tag(source, ln)
        if tag["close"]:
            raise self.err(
                f"unexpected closing tag '{tag['raw_close']}'",
                ln.no,
                ln.indent,
                hint="Remove it or open a matching element first.",
            )
        if tag["name"] == "style":
            return self._build_style(lines, pos, j, ln, tag, in_component)
        if tag["name"] == "slot":
            return self._build_slot(tag, ln, j)
        if tag["selfclose"]:
            node = self._make_node(tag, ln, [], in_component)
            return node, j + 1
        children, k2 = self._build_block(lines, j + 1, ln.indent, in_component, False)
        # optional explicit close tag
        if k2 < len(lines):
            nxt = lines[k2]
            if nxt.kind == "MARKUP" and nxt.stripped.startswith("</") and nxt.indent == ln.indent:
                name = re.match(r"^</\s*([A-Za-z][\w.-]*|<>)?", nxt.stripped)
                cname = (name.group(1) if name else "") or ""
                if cname.strip("<>") != tag["name"].strip("<>"):
                    raise self.err(
                        f"mismatched closing tag: expected '</{tag['name']}>' but found '{nxt.stripped}'",
                        nxt.no,
                        nxt.indent,
                        hint=f"Change it to '</{tag['name']}>'.",
                    )
                k2 += 1
        node = self._make_node(tag, ln, children, in_component)
        return node, k2

    def _build_style(self, lines, pos, j, ln, tag, in_component):
        if tag["attrs"]:
            raise self.err(
                "<style> takes no attributes",
                ln.no,
                ln.indent,
                hint="Write plain CSS between <style> and </style>.",
            )
        css_lines = []
        k = j + 1
        while k < len(lines):
            cur = lines[k]
            if cur.kind == "MARKUP" and cur.stripped.startswith("</") and cur.indent == ln.indent:
                break
            if cur.kind != "CSS" or cur.indent <= ln.indent:
                raise self.err(
                    "only CSS may appear inside <style>",
                    cur.no,
                    cur.indent,
                    hint="Indent CSS under <style> and close with </style>.",
                )
            css_lines.append(cur)
            k += 1
        if k >= len(lines):
            raise self.err(
                "unclosed <style> block: missing </style>",
                ln.no,
                ln.indent,
                hint="Add a </style> line at the same indent as <style>.",
            )
        dedent = min(l.indent for l in css_lines) if css_lines else 0
        css = "\n".join(l.text[dedent:] if len(l.text) >= dedent else l.text for l in css_lines)
        node = Style(css=css, span=self.span_for(ln.no, ln.indent, lines[k].no))
        return node, k + 1

    def _build_slot(self, tag, ln, end: int) -> tuple[Slot, int]:
        # slot is self-closing or empty; children not allowed
        name = "default"
        for a in tag["attrs"]:
            if a.name == "name" and isinstance(a.value, Static):
                name = a.value.text
            else:
                raise self.err(
                    "<slot> only accepts name=\"...\"",
                    ln.no,
                    ln.indent,
                    hint='Use <slot /> or <slot name="header" />.',
                )
        return Slot(name=name, span=self.span_for(ln.no, ln.indent), hid=self.next_hid()), end + 1

    def _make_node(self, tag, ln, children, in_component):
        name = tag["name"]
        span = self.span_for(ln.no, ln.indent)
        if name == "<>":
            el = Element(tag="<>", attrs=[], children=children, span=span, hid=self.next_hid())
            return el
        if name[:1].isupper():
            props: dict[str, Attr] = {}
            slots: dict[str, list] = {}
            for a in tag["attrs"]:
                if a.name == "slot":
                    continue
                if a.name in props:
                    raise self.err(
                        f"duplicate prop '{a.name}'",
                        a.span.start_line,
                        a.span.start_col,
                        hint="Remove one of the duplicates.",
                    )
                props[a.name] = a
            default: list = []
            for ch in children:
                sname = getattr(ch, "slot_name", "")
                if sname:
                    slots.setdefault(sname, []).append(ch)
                else:
                    default.append(ch)
            # 'slot' attr routing for elements
            routed_default: list = []
            for ch in default:
                routed_default.append(ch)
            return CompUse(name=name, props=props, children=routed_default, span=span, hid=self.next_hid())
        attrs = tag["attrs"]
        el = Element(tag=name, attrs=attrs, children=children, span=span, hid=self.next_hid())
        # route children carrying slot="name" (only meaningful inside components,
        # but harmless to record everywhere)
        kept: list = []
        for ch in children:
            sname = ""
            if isinstance(ch, Element):
                for a in ch.attrs:
                    if a.name == "slot" and isinstance(a.value, Static):
                        sname = a.value.text
            if sname:
                ch.slot_name = sname  # type: ignore[attr-defined]
                ch.attrs = [a for a in ch.attrs if a.name != "slot"]
            kept.append(ch)
        el.children = kept
        return el

    # -- tag tokenizer ----------------------------------------------------------
    def _parse_tag(self, source: str, ln: _Line) -> dict:
        text = source.strip()
        lines = source.split("\n")
        offsets = []
        off = 0
        for part in lines:
            offsets.append(off)
            off += len(part) + 1

        def linecol(index: int) -> tuple[int, int]:
            li = 0
            for i, o in enumerate(offsets):
                if o <= index:
                    li = i
            base = self.raw[ln.no + li - 1] if ln.no + li - 1 < len(self.raw) else ""
            col = index - offsets[li] + (len(base) - len(base.lstrip()))
            return ln.no + li, max(0, col)

        if text.startswith("</"):
            m = re.match(r"^</\s*([A-Za-z][\w.-]*|<>)?\s*>?\s*$", text)
            cname = m.group(1) if m and m.group(1) else ""
            return {"close": True, "raw_close": text, "name": cname or ""}
        if text == "<>" or text.startswith("<>"):
            rest = text[2:].strip()
            if rest and rest != ">":
                raise self.err(
                    "fragments <> take no attributes",
                    ln.no,
                    ln.indent,
                    hint="Put attributes on a real element inside the fragment.",
                )
            return {"close": False, "name": "<>", "attrs": [], "selfclose": False}
        m = re.match(r"^<\s*([A-Za-z][\w.-]*)", text)
        if not m:
            raise self.err(
                f"invalid tag '{text[:24]}'",
                ln.no,
                ln.indent,
                hint="Tag names start with a letter, e.g. <div>, <MyComp>, <>.",
            )
        name = m.group(1)
        if not _TAG_NAME_RE.match(name):
            raise self.err(
                f"invalid tag name '{name}'",
                ln.no,
                ln.indent,
                hint="Use letters, digits, '-' or '.', e.g. <my-el>.",
            )
        # find the end of the opening tag: first '>' outside quotes/{...}
        end = self._open_tag_end(text, m.end())
        if end is None:
            raise self.err(
                f"unclosed tag '<{name}>'",
                ln.no,
                ln.indent,
                hint=f"End the tag with '>' (children) or '/>' (self-closing).",
            )
        body = text[m.end() : end]
        rest = text[end + 1 :].strip()
        selfclose = body.rstrip().endswith("/")
        if selfclose:
            body = body.rstrip()[: -1]
        elif rest and not rest.startswith("</"):
            # inline content belongs to the element, not the tag
            pass
        base_off = text.index(name) + len(name)
        attrs = self._parse_attrs(body, base_off, text, ln, linecol, name)
        return {"close": False, "name": name, "attrs": attrs, "selfclose": selfclose}

    @staticmethod
    def _tag_name_of(open_tag: str) -> str:
        m = re.match(r"^<\s*([A-Za-z][\w.-]*)", open_tag)
        return m.group(1) if m else "?"

    @staticmethod
    def _open_tag_end(text: str, start: int) -> int | None:
        """Index of the '>' closing the opening tag (skips quotes/{...})."""
        i, n = start, len(text)
        in_str: str | None = None
        brace = 0
        while i < n:
            c = text[i]
            if in_str:
                if c == "\\":
                    i += 2
                    continue
                if c == in_str:
                    in_str = None
            elif brace > 0:
                if c in "\"'":
                    in_str = c
                elif c == "{":
                    brace += 1
                elif c == "}":
                    brace -= 1
            elif c in "\"'":
                in_str = c
            elif c == "{":
                brace += 1
            elif c == ">":
                return i
            i += 1
        return None

    def _parse_attrs(self, body, base_off, full_text, ln, linecol, tag_name) -> list[Attr]:
        attrs: list[Attr] = []
        i, n = 0, len(body)
        while True:
            while i < n and body[i].isspace():
                i += 1
            if i >= n:
                break
            if body[i] == "/":
                raise self.err(
                    f"unexpected '/' in tag '<{tag_name}>'",
                    ln.no,
                    ln.indent,
                    hint="Self-close with '/>' at the very end of the tag.",
                )
            m = re.match(r"[A-Za-z_@][\w.:@-]*", body[i:])
            if not m:
                ll, cc = linecol(base_off + i)
                raise self.err(
                    f"invalid attribute near '{body[i:i+12]}'",
                    ll,
                    cc,
                    hint="Attributes look like name=\"...\", name={expr}, or a bare flag.",
                )
            aname = m.group(0)
            if not _ATTR_NAME_RE.match(aname):
                ll, cc = linecol(base_off + i)
                raise self.err(
                    f"invalid attribute name '{aname}'",
                    ll,
                    cc,
                    hint="Use letters, digits, '-', '.', ':' or '@'.",
                )
            ll, cc = linecol(base_off + i)
            aspan = Span(
                file=self.filename,
                start_line=ll,
                start_col=cc,
                end_line=ll,
                end_col=cc + len(aname),
                snippet=self.snippets.get(ll, ""),
            )
            i += len(aname)
            while i < n and body[i].isspace():
                i += 1
            value: Static | Dyn | HandlerRef | BindRef | CssStatic | CssDyn = Static("")
            has_value = False
            if i < n and body[i] == "=":
                has_value = True
                i += 1
                while i < n and body[i].isspace():
                    i += 1
                if i >= n:
                    raise self.err(
                        f"attribute '{aname}' is missing a value",
                        aspan.start_line,
                        aspan.start_col,
                        hint=f'Use {aname}="..." or {aname}={{expr}}.',
                    )
                value, i = self._parse_attr_value(body, i, aname, aspan, tag_name)
            attrs.append(self._classify_attr(aname, value, has_value, aspan, tag_name))
        return attrs

    def _parse_attr_value(self, body, i, aname, aspan, tag_name):
        n = len(body)
        if body[i] in "\"'":
            q = body[i]
            i += 1
            seq: list[tuple[str, str]] = []  # ("t", text) | ("e", code)
            cur: list[str] = []
            closed = False
            while i < n:
                c = body[i]
                if c == "\\" and i + 1 < n:
                    cur.append(body[i + 1])
                    i += 2
                    continue
                if c == q:
                    closed = True
                    i += 1
                    break
                if c == "{":
                    code, i = self._balanced(body, i, "{", "}")
                    if cur:
                        seq.append(("t", "".join(cur)))
                        cur = []
                    seq.append(("e", code))
                    continue
                cur.append(c)
                i += 1
            if not closed:
                raise self.err(
                    f"unterminated string for attribute '{aname}'",
                    aspan.start_line,
                    aspan.start_col,
                    hint="Close the quote.",
                )
            if cur:
                seq.append(("t", "".join(cur)))
            if any(k == "e" for k, _ in seq):
                return Dyn(code=self._parts_to_fstring(seq), line=aspan.start_line), i
            return Static("".join(v for _, v in seq)), i
        if body[i] == "{":
            code, i = self._balanced(body, i, "{", "}")
            if not code.strip():
                raise self.err(
                    f"empty {{}} in attribute '{aname}'",
                    aspan.start_line,
                    aspan.start_col,
                    hint=f"Put an expression inside, e.g. {aname}={{value}}.",
                )
            return Dyn(code=code.strip(), line=aspan.start_line), i
        m = re.match(r"[A-Za-z_][\w.-]*", body[i:])
        if m:
            return Static(m.group(0)), i + len(m.group(0))
        raise self.err(
            f"invalid value for attribute '{aname}'",
            aspan.start_line,
            aspan.start_col,
            hint=f'Use {aname}="..." or {aname}={{expr}}.',
        )

    @staticmethod
    def _parts_to_fstring(seq: list[tuple[str, str]]) -> str:
        out = ["f\""]
        for kind, val in seq:
            if kind == "t":
                out.append(val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n"))
            else:
                out.append("{" + val + "}")
        out.append("\"")
        return "".join(out)

    @staticmethod
    def _balanced(body: str, i: int, open_c: str, close_c: str) -> tuple[str, int]:
        assert body[i] == open_c
        depth = 0
        j = i
        n = len(body)
        in_str: str | None = None
        while j < n:
            c = body[j]
            if in_str:
                if c == "\\":
                    j += 2
                    continue
                if c == in_str:
                    in_str = None
            elif c in "\"'":
                in_str = c
            elif c == open_c:
                depth += 1
            elif c == close_c:
                depth -= 1
                if depth == 0:
                    return body[i + 1 : j], j + 1
            j += 1
        raise ValueError(f"unbalanced {open_c}{close_c}")

    def _classify_attr(self, aname, value, has_value, aspan, tag_name) -> Attr:
        if aname == "bind":
            if not isinstance(value, Dyn) or not _ON_NAME_RE.match(value.code):
                raise self.err(
                    "bind={...} must name a signal",
                    aspan.start_line,
                    aspan.start_col,
                    hint="Use bind={my_signal} where my_signal = signal(...).",
                )
            return Attr(aname, BindRef(value.code), aspan)
        if aname.startswith("on"):
            if isinstance(value, Dyn):
                if _ON_NAME_RE.match(value.code):
                    return Attr(aname, HandlerRef(value.code), aspan)
                return Attr(aname, value, aspan)
            raise self.err(
                f"{aname}={{...}} must reference a handler or expression",
                aspan.start_line,
                aspan.start_col,
                hint=f"Use {aname}={{my_handler}} or {aname}={{count.set(0)}}.",
            )
        if aname == "css":
            if isinstance(value, Dyn):
                if _ON_NAME_RE.match(value.code):
                    # css={name} ambiguous: treat bare name as dynamic too
                    return Attr(aname, CssDyn(code=value.code, line=value.line), aspan)
                # f-string synthesized from interpolation counts as dynamic
                if value.code.startswith("f\""):
                    return Attr(aname, CssDyn(code=value.code, line=value.line), aspan)
                return Attr(aname, CssDyn(code=value.code, line=value.line), aspan)
            if isinstance(value, Static):
                if not has_value:
                    raise self.err(
                        "bare 'css' needs a value",
                        aspan.start_line,
                        aspan.start_col,
                        hint='Use css="color: red;" or css={expr}.',
                    )
                return Attr(aname, CssStatic(value.text), aspan)
        if aname == "key":
            if isinstance(value, Dyn):
                return Attr(aname, value, aspan)
            if isinstance(value, Static) and has_value:
                return Attr(aname, value, aspan)
            raise self.err(
                "key needs a value",
                aspan.start_line,
                aspan.start_col,
                hint="Use key={item.id}.",
            )
        if isinstance(value, Static) and not has_value:
            return Attr(aname, Static(""), aspan)
        return Attr(aname, value, aspan)

    # -- text ------------------------------------------------------------------
    def _build_text(self, ln: _Line) -> list:
        self._text_lines.add(ln.no)
        text = ln.stripped
        if text.startswith("<!--") and text.endswith("-->"):
            return []
        parts = self._split_interp(text, ln)
        if len(parts) == 1 and parts[0][0] == "t":
            if not parts[0][1].strip():
                return []
            return [Text(content=parts[0][1], span=self.span_for(ln.no, ln.indent), hid=self.next_hid())]
        nodes: list = []
        for kind, val in parts:
            if kind == "t":
                if val:
                    nodes.append(
                        Text(content=val, span=self.span_for(ln.no, ln.indent), hid=self.next_hid())
                    )
            else:
                nodes.append(
                    DynText(
                        code=val, line=ln.no, span=self.span_for(ln.no, ln.indent), hid=self.next_hid()
                    )
                )
        return nodes

    def _split_interp(self, text: str, ln: _Line) -> list[tuple[str, str]]:
        parts: list[tuple[str, str]] = []
        i, n = 0, len(text)
        buf: list[str] = []
        while i < n:
            c = text[i]
            if c == "{" and i + 1 < n and text[i + 1] == "{":
                buf.append("{")
                i += 2
                continue
            if c == "}" and i + 1 < n and text[i + 1] == "}":
                buf.append("}")
                i += 2
                continue
            if c == "{":
                try:
                    code, j = self._balanced(text, i, "{", "}")
                except ValueError:
                    raise self.err(
                        "unbalanced '{' in text",
                        ln.no,
                        ln.indent,
                        hint="Escape literal braces as '{{' and '}}'.",
                    )
                if not code.strip():
                    raise self.err(
                        "empty {} in text",
                        ln.no,
                        ln.indent,
                        hint="Put an expression inside, e.g. {title}.",
                    )
                if buf:
                    parts.append(("t", "".join(buf)))
                    buf = []
                parts.append(("e", code.strip()))
                i = j
                continue
            buf.append(c)
            i += 1
        if buf:
            parts.append(("t", "".join(buf)))
        return parts


def parse_text(source: str, filename: str = "<input>") -> Document:
    return Parser(source, filename).parse()


def parse_file(path: str) -> Document:
    with open(path, encoding="utf-8") as f:
        return parse_text(f.read(), filename=path)
