"""Compiled server rendering: a page's UI tree becomes one Python function.

:class:`pyweb.ssr.Renderer` walks the tree and ``eval``\\ s every ``{expression}``
in a fresh copy of the module's globals. That is simple and it is the reference
behaviour, but on a real page it was a third of the request time. Here the same
tree is turned into Python source once:

* text and literal attributes are escaped at compile time;
* each ``{expression}`` is inlined as real bytecode;
* ``for`` and ``if`` become real loops and branches, so loop variables are fast
  locals and comprehensions inside expressions work as in any function.

The output is byte-for-byte what the interpreter produces (a differential test
renders every example both ways). Node kinds that are rare and not hot
(``<Markdown>``, layout slots) are handed back to the interpreter.

Errors keep the interpreter's message and line: each generated line remembers
the template expression it came from.
"""

from __future__ import annotations

import html as _html
import itertools
import re
import sys
import types

from .compiler.ast import ControlFor, ControlIf, Element, ExprNode, MarkdownNode, SlotNode, TextNode
from .ssr import SPREAD, _own_class, explicit_names

_counter = itertools.count()
_esc = _html.escape


class _Gen:
    def __init__(self):
        self.lines: list[str] = []
        self.where: dict[int, tuple[str, int]] = {}    # generated line -> (template code, template line)
        self.nodes: list = []                           # nodes handed to the interpreter
        self.ids = itertools.count()
        self.bound: list[set] = [set()]                 # loop variables in scope (for interpreter fallbacks)

    def emit(self, depth, text, source=None):
        self.lines.append("    " * depth + text)
        if source is not None:
            self.where[len(self.lines)] = source

    def var(self, stem):
        return f"_pw_{stem}{next(self.ids)}"

    # ---------------------------------------------------------------- nodes
    def block(self, nodes, depth, out):
        for n in nodes:
            self.node(n, depth, out)

    def node(self, n, depth, out):
        if isinstance(n, TextNode):
            text = _esc(n.text, quote=False)
            if text:
                self.emit(depth, f"{out}({text!r})")
        elif isinstance(n, ExprNode):
            self.emit(depth, f"{out}(_text(({n.code})))", (n.code, n.line))
        elif isinstance(n, ControlFor):
            # The body is its own function: loop variables exist only inside the loop, exactly as
            # in the interpreter (a page variable with the same name is untouched outside it).
            items, body, item = self.var("items"), self.var("loop"), self.var("item")
            self.emit(depth, f"def {body}({item}):")
            self.emit(depth + 1, f"{n.target} = {item}", (n.target, n.line))
            names = set(re.findall(r"[A-Za-z_]\w*", n.target))
            self.bound.append(self.bound[-1] | names)
            self.block(n.body, depth + 1, out)
            self.bound.pop()
            self.emit(depth, f"{items} = ({n.iterable})", (n.iterable, n.line))
            self.emit(depth, f"if {items} is not None:")
            self.emit(depth + 1, f"for {item} in {items}:")
            self.emit(depth + 2, f"{body}({item})")
        elif isinstance(n, ControlIf):
            self.emit(depth, f"if ({n.test}):", (n.test, n.line))
            self.block(n.body, depth + 1, out)
            self.emit(depth + 1, "pass")
            if n.orelse:
                self.emit(depth, "else:")
                self.block(n.orelse, depth + 1, out)
                self.emit(depth + 1, "pass")
        elif isinstance(n, Element):
            if n.is_component:
                self.component(n, depth, out)
            elif "data-pw-boundary" in n.attrs:
                self.fallback(n, depth, out)          # <ErrorBoundary>: the interpreter catches errors
            else:
                self.element(n, depth, out)
        elif isinstance(n, (MarkdownNode, SlotNode)):
            self.fallback(n, depth, out)

    def fallback(self, n, depth, out):
        """Render ``n`` with the interpreter, in a scope that includes the loop variables."""
        idx = len(self.nodes)
        self.nodes.append(n)
        names = sorted(self.bound[-1])
        scope = "_g" if not names else "dict(_g, " + ", ".join(f"{k}={k}" for k in names) + ")"
        self.emit(depth, f"{out}(_r.node(_nodes[{idx}], {scope}))", ("<markdown>", n.line))

    def component(self, n, depth, out):
        props = self.var("props")
        self.emit(depth, f"{props} = {{}}")
        for key, val in n.attrs.items():
            if key.startswith(SPREAD):
                d = self.var("spread")
                self.emit(depth, f"{d} = dict(({val[1]}) or {{}})", (val[1], val[2]))
                self.emit(depth, f"for _k, _v in {d}.items():")
                self.emit(depth + 1, f"if _k not in {sorted(n.attrs)!r}: {props}.setdefault(_k, _v)")
            elif val is True:
                self.emit(depth, f"{props}[{key!r}] = True")
            elif val[0] == "lit":
                self.emit(depth, f"{props}[{key!r}] = {val[1]!r}")
            else:
                self.emit(depth, f"{props}[{key!r}] = ({val[1]})", (val[1], val[2]))
        if n.children:
            buf = self.var("kids")
            self.emit(depth, f"{buf} = []")
            self.block(n.children, depth, f"{buf}.append")
            self.emit(depth, f"{props}['children'] = _Markup(''.join({buf}))")
        self.emit(depth, f"{out}(_r.component_html({n.tag!r}, {props}, _g.get('__pyweb_ns__')))",
                  (f"<{n.tag}>", n.line))

    def element(self, n, depth, out):
        tag = n.tag
        low = tag.lower()
        self.attrs(n, depth, out)
        if low in VOID:
            self.emit(depth, f"{out}('>')")
            return
        bind = n.attrs.get("bind")
        if low == "textarea" and isinstance(bind, tuple):
            self.emit(depth, f"{out}('>')")
            self.emit(depth, f"{out}(_plain(({bind[1]})))", (bind[1], n.line))
            self.emit(depth, f"{out}({'</' + tag + '>'!r})")
            return
        if low == "select" and isinstance(bind, tuple):
            buf = self.var("opts")
            self.emit(depth, f"{buf} = []")
            self.block(n.children, depth, f"{buf}.append")
            self.emit(depth, f"{out}('>' + _select(''.join({buf}), ({bind[1]})) + {'</' + tag + '>'!r})",
                      (bind[1], n.line))
            return
        self.emit(depth, f"{out}('>')")
        self.block(n.children, depth, out)
        self.emit(depth, f"{out}({'</' + tag + '>'!r})")

    def attrs(self, n, depth, out):
        """``<tag`` and its attributes; static parts are pre-rendered."""
        static = "<" + n.tag
        has_spread = any(k.startswith(SPREAD) for k in n.attrs)
        spread_done = False
        for key, val in n.attrs.items():
            if key.startswith(SPREAD):
                if spread_done:
                    continue
                spread_done = True
                if static:
                    self.emit(depth, f"{out}({static!r})")
                    static = ""
                own = _own_class(n.attrs)
                own_code = "None" if own is None else (repr(own[1]) if own[0] == "lit" else f"({own[1]})")
                dicts = ", ".join(f"({v[1]})" for k, v in n.attrs.items() if k.startswith(SPREAD))
                self.emit(depth, f"{out}(_spread({explicit_names(n.attrs)!r}, {own_code}, [{dicts}]))",
                          (val[1], val[2]))
                continue
            if has_spread and key in ("class", "class_", "className"):
                continue
            if key.startswith("on") or key == "ref":
                continue
            if key == "bind":
                if not isinstance(val, tuple):
                    continue
                if static:
                    self.emit(depth, f"{out}({static!r})")
                    static = ""
                typ = n.attrs.get("type")
                typ = typ[1] if isinstance(typ, tuple) else None
                own = n.attrs.get("value")
                own = own[1] if isinstance(own, tuple) else None
                self.emit(depth, f"{out}(_bind({n.tag.lower()!r}, {typ!r}, {own!r}, ({val[1]})))",
                          (val[1], val[2]))
                continue
            name = "class" if key in ("class_", "className") else key
            if val is True:
                static += f" {name}"
                continue
            kind, body, line = val
            dynamic_href = name == "href" and n.tag == "a" and "aria-current" not in n.attrs
            if kind == "lit" and not dynamic_href:
                static += _static_attr(name, body)
                continue
            if static:
                self.emit(depth, f"{out}({static!r})")
                static = ""
            value = repr(body) if kind == "lit" else f"({body})"
            fn = "_href" if dynamic_href else "_attr"
            self.emit(depth, f"{out}({fn}({name!r}, {value}))", (body, line))
        if static:
            self.emit(depth, f"{out}({static!r})")


VOID = {"input", "img", "br", "hr", "meta", "link", "source", "wbr",
        "col", "base", "area", "embed", "track", "param"}


def _static_attr(name, v):
    from .ssr import URL_ATTRS, safe_url
    if name == "class" and not v:
        return ""
    if name in URL_ATTRS:
        v = safe_url(v)
    return f' {name}="{_esc(str(v))}"'


def compile_ui(nodes, label="ui"):
    """Python source for rendering ``nodes``, compiled; returns a :class:`Compiled`."""
    gen = _Gen()
    gen.emit(1, "_o = []")
    gen.emit(1, "_a = _o.append")
    gen.block(nodes, 1, "_a")
    gen.emit(1, "return ''.join(_o)")
    header = ("def _render(_g, _r, _nodes, _text, _attr, _href, _bind, _plain, _select, _Markup, _spread):")
    source = header + "\n" + "\n".join(gen.lines) + "\n"
    filename = f"<pyweb-ssr-{next(_counter)}:{label}>"
    code = compile(source, filename, "exec")
    fn_code = next(c for c in code.co_consts if isinstance(c, types.CodeType))
    # Line numbers in tracebacks: header is line 1, emitted lines start at 2.
    where = {k + 1: v for k, v in gen.where.items()}
    return Compiled(fn_code, filename, where, tuple(gen.nodes), source)


class Compiled:
    __slots__ = ("code", "filename", "where", "nodes", "source")

    def __init__(self, code, filename, where, nodes, source):
        self.code, self.filename, self.where, self.nodes, self.source = code, filename, where, nodes, source

    def locate(self, tb):
        """The template (code, line) where the exception in traceback ``tb`` happened, if it's ours."""
        found = None
        while tb is not None:
            if tb.tb_frame.f_code.co_filename == self.filename:
                found = self.where.get(tb.tb_lineno, found)
            tb = tb.tb_next
        return found


def run(compiled, renderer, scope, helpers):
    """Render with ``compiled`` in ``scope`` (module globals + the page's variables)."""
    fn = types.FunctionType(compiled.code, scope, "_render")
    from .ssr import RenderError
    try:
        return fn(scope, renderer, compiled.nodes, *helpers)
    except RenderError:
        raise
    except Exception as exc:  # noqa: BLE001
        where = compiled.locate(sys.exc_info()[2])
        if where is None:
            raise
        code, line = where
        raise RenderError(f"{{{code}}} raised {type(exc).__name__}: {exc}", line) from exc
