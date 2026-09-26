"""Emit client JS: signals boot, live regions, handlers, hydration, overlay hook."""
from __future__ import annotations

import json

from pyweb.compiler.ast import (
    Attr,
    BindRef,
    CompUse,
    Cond,
    CssDyn,
    Dyn,
    DynText,
    Element,
    For,
    HandlerRef,
    Slot,
    Text,
)
from pyweb.compiler.codegen import expr as lowering


class JSGen:
    def __init__(self, analysis, filename: str, route: str = "/"):
        self.a = analysis
        self.filename = filename
        self.route = route
        self.lines: list[str] = []
        self.map = None  # SourceMap, set by pipeline
        self._gen = 0
        self._scopes: list[dict[str, str]] = [{}]
        self._loop_vars: list[str] = []

    # -- output ----------------------------------------------------------
    def w(self, text: str, src_line: int = 0, src_col: int = 0) -> None:
        self._gen += 1
        self.lines.append(text)
        if self.map is not None and src_line:
            self.map.add(self._gen, 0, src_line, src_col)

    def scopes(self) -> set[str]:
        out: dict[str, str] = {}
        for s in self._scopes:
            out.update(s)
        return set(out)

    # -- entry ------------------------------------------------------------
    def generate(self, root_nodes: list, boot: dict) -> str:
        self.w('"use strict";', 0)
        self.w(f"/* generated from {self.filename} :: {self.route} */", 0)
        # signals boot
        for name, init in boot.get("signals", {}).items():
            self.w(f"const {name} = PyWeb.signal({init});", boot.get("line", 0))
        for name, fn in boot.get("computed", {}).items():
            body = fn if isinstance(fn, str) else "null"
            self.w(f"const {name} = PyWeb.computed(() => ({body}));", boot.get("line", 0))
        # handlers (rpc stubs)
        for name, spec in boot.get("handlers", {}).items():
            args = ", ".join(spec.get("args", []))
            self.w(
                f"async function {name}({args}) {{ const r = await fetch({json.dumps(spec['url'])}, "
                f"{{method: 'POST', headers: {{'content-type': 'application/json'}}, "
                f"body: JSON.stringify({{args: [{args}]}})}}); return r.json(); }}",
                0,
            )
        self.w("document.addEventListener('DOMContentLoaded', () => {", 0)
        for node in root_nodes:
            self.emit(node, "document.getElementById('app')", True)
        self.w("});", 0)
        self.w("window.__pyweb_error = PyWeb.makeErrorHook(window.__pyweb_sourcemap || null);", 0)
        return "\n".join(self.lines) + "\n"

    # -- nodes --------------------------------------------------------------
    def emit(self, node, parent_js: str, is_root: bool = False) -> None:
        if isinstance(node, Text):
            if node.content.strip():
                self.w(
                    f"{parent_js}.insertAdjacentHTML('beforeend', {json.dumps(node.content)});",
                    node.span.start_line,
                )
            return
        if isinstance(node, DynText):
            tmp = f"__t{node.hid}"
            self.w(f"const {tmp} = document.createTextNode('');", node.span.start_line)
            self.w(f"{parent_js}.appendChild({tmp});", node.span.start_line)
            js = self.js_expr(node.code, node.line)
            self.w(f"PyWeb.dynText({tmp}, () => ({js}));", node.line)
            return
        if isinstance(node, Element):
            self.emit_element(node, parent_js)
            return
        if isinstance(node, For):
            self.emit_for(node, parent_js)
            return
        if isinstance(node, Cond):
            self.emit_cond(node, parent_js)
            return
        if isinstance(node, CompUse):
            self.emit_comp(node, parent_js)
            return
        if isinstance(node, Slot):
            self.w(f"/* slot {node.name} filled by caller */", node.span.start_line)
            return

    def emit_element(self, node: Element, parent_js: str) -> None:
        var = f"__e{node.hid}"
        if node.tag == "<>":
            self.w(f"const {var} = document.createDocumentFragment();", node.span.start_line)
        else:
            self.w(f"const {var} = document.createElement({json.dumps(node.tag)});", node.span.start_line)
        for attr in node.attrs:
            self.emit_attr(node, var, attr)
        if node.css_class:
            self.w(f"{var}.classList.add({json.dumps(node.css_class)});", node.span.start_line)
        for child in node.children:
            self.emit(child, var)
        if node.tag == "<>":
            # fragments: append children (already appended to fragment)
            self.w(f"{parent_js}.appendChild({var});", node.span.start_line)
        else:
            self.w(f"{parent_js}.appendChild({var});", node.span.start_line)

    def emit_attr(self, node: Element, var: str, attr: Attr) -> None:
        from pyweb.compiler.ast import CssStatic

        v = attr.value
        name = attr.name
        line = attr.span.start_line
        if isinstance(v, BindRef):
            if name in ("value", "checked"):
                self.w(
                    f"{var}.{name} = {v.name}.get(); "
                    f"{v.name}.subscribe(() => {{ {var}.{name} = {v.name}.get(); }});",
                    line,
                )
                self.w(
                    f"{var}.addEventListener('input', () => {v.name}.set({var}.{name}));",
                    line,
                )
            else:
                self.w(f"PyWeb.dynAttr({var}, {json.dumps(name)}, () => {v.name}.get());", line)
            return
        if isinstance(v, HandlerRef):
            ev = name[2:].lower() if name.lower().startswith("on") else name
            self.w(f"{var}.addEventListener({json.dumps(ev)}, () => {v.name}());", line)
            return
        if isinstance(v, Dyn):
            js = self.js_expr(v.code, v.line)
            if name.lower().startswith("on"):
                ev = name[2:].lower()
                self.w(f"{var}.addEventListener({json.dumps(ev)}, () => ({js}));", v.line)
            else:
                self.w(f"PyWeb.dynAttr({var}, {json.dumps(name)}, () => ({js}));", v.line)
            return
        if isinstance(v, CssDyn):
            js = self.js_expr(v.code, v.line)
            self.w(f"PyWeb.effect(() => {{ {var}.style.cssText = ({js}); }});", v.line)
            return
        if isinstance(v, CssStatic):
            self.w(f"{var}.style.cssText += {json.dumps(v.css)};", line)
            return
        # Static
        if name.startswith("@") or name.startswith(":"):
            return
        if v.text == "" and name not in ("alt", "value", "placeholder"):
            self.w(f"{var}.setAttribute({json.dumps(name)}, '');", line)
        else:
            self.w(f"{var}.setAttribute({json.dumps(name)}, {json.dumps(v.text)});", line)

    def emit_for(self, node: For, parent_js: str) -> None:
        anchor = f"__a{node.hid}"
        self.w(f"const {anchor} = document.createComment('for');", node.span.start_line)
        self.w(f"{parent_js}.appendChild({anchor});", node.span.start_line)
        iter_js = self.js_expr(node.iter_code, node.iter_line)
        key_js = "undefined"
        if node.key_code:
            saved = list(self._loop_vars)
            self._loop_vars.append(node.var)
            k = self.js_expr(node.key_code, node.span.start_line)
            self._loop_vars[:] = saved
            key_js = f"(({node.var}) => ({k}))"
        row_fn = f"__row{node.hid}"
        self.w(f"const {row_fn} = ({node.var}, __i, __key) => {{", node.span.start_line)
        self.w("  const __frag = document.createDocumentFragment();", node.span.start_line)
        self._scopes.append({node.var: "loop"})
        self._loop_vars.append(node.var)
        for child in node.body:
            self.emit(child, "__frag")
        self._loop_vars.pop()
        self._scopes.pop()
        self.w("  const __wrap = document.createDocumentFragment();", node.span.start_line)
        self.w("  __wrap.appendChild(__frag);", node.span.start_line)
        self.w("  return { el: __wrap };", node.span.start_line)
        self.w("};", node.span.start_line)
        self.w(
            f"PyWeb.liveList({anchor}, () => ({iter_js}), "
            f"(__item, __i, __key) => {row_fn}(__item, __i, __key), {key_js});",
            node.iter_line,
        )

    def emit_cond(self, node: Cond, parent_js: str) -> None:
        anchor = f"__c{node.hid}"
        self.w(f"const {anchor} = document.createComment('if');", node.span.start_line)
        self.w(f"{parent_js}.appendChild({anchor});", node.span.start_line)
        pickers = []
        for idx, branch in enumerate(node.branches):
            if branch.cond_code is None:
                pickers.append("true")
            else:
                pickers.append(f"({self.js_expr(branch.cond_code, branch.cond_line)})")
        pick_js = " : ".join(f"({c}) ? {i}" for i, c in enumerate(pickers)) + f" : -1"
        # branches
        branch_fns = []
        for idx, branch in enumerate(node.branches):
            fn = f"__br{node.hid}_{idx}"
            branch_fns.append(fn)
            self.w(f"function {fn}(__disposers) {{", branch.span.start_line)
            self.w("  const __frag = document.createDocumentFragment();", branch.span.start_line)
            for child in branch.body:
                self.emit(child, "__frag")
            # collect child nodes into an array for liveIf
            self.w("  return Array.from(__frag.childNodes);", branch.span.start_line)
            self.w("}", branch.span.start_line)
        self.w(
            f"PyWeb.liveIf({anchor}, () => ({pick_js}), [{', '.join(branch_fns)}]);",
            node.span.start_line,
        )

    def emit_comp(self, node: CompUse, parent_js: str) -> None:
        decl = self.a.components.get(node.name)
        props_js = []
        for pname, attr in node.props.items():
            v = attr.value
            if isinstance(v, BindRef):
                props_js.append(f"{pname}: {v.name}.get()")
            elif isinstance(v, HandlerRef):
                props_js.append(f"{pname}: {v.name}")
            elif isinstance(v, Dyn):
                props_js.append(f"{pname}: ({self.js_expr(v.code, v.line)})")
            elif isinstance(v, CssDyn):
                props_js.append(f"{pname}: ({self.js_expr(v.code, v.line)})")
            else:
                props_js.append(f"{pname}: {json.dumps(v.text)}")
        # defaults for missing optional props
        if decl:
            for pname, spec in decl.props.items():
                if pname not in node.props and spec.get("default") is not None:
                    props_js.append(f"{pname}: {spec['default']}")
        self.w(
            f"/* component <{node.name}> props {{{', '.join(props_js)}}} (ssr inlined) */",
            node.span.start_line,
        )
        for child in node.children:
            self.emit(child, parent_js)

    # -- expressions -----------------------------------------------------------
    def js_expr(self, code: str, line: int, extra_vars: dict | None = None) -> str:
        from pyweb.compiler.codegen.expr import LowerError

        scope = self.scopes()
        if extra_vars:
            scope = set(scope) | set(extra_vars)
        try:
            return lowering.to_js(code, scope, set(self.a.signals), set(self._loop_vars))
        except LowerError as e:
            from pyweb.compiler.ast import CompileError, Span

            raise CompileError(
                f"cannot compile expression {code!r} to JS: {e}",
                Span(file=self.filename, start_line=line, start_col=0, snippet=""),
                hint="Use simple attribute access, calls, and operators in markup.",
            )
