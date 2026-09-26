"""Semantic analysis: signals, handlers, components, prop validation."""
from __future__ import annotations

import ast as pyast
import re

from pyweb.compiler.ast import (
    Attr,
    BindRef,
    CompUse,
    CompileError,
    Cond,
    CssDyn,
    Document,
    Dyn,
    DynText,
    Element,
    For,
    HandlerRef,
    Root,
    Span,
    Style,
    Text,
    iter_nodes,
)
from pyweb.compiler.reactivity import validate_expr
from pyweb.compiler.rpc import collect_handlers


class ComponentDecl:
    def __init__(self, name, props: dict[str, dict], deco_line: int):
        self.name = name
        self.props = props  # prop -> {"required": bool, "default": str|None, "line": int}
        self.deco_line = deco_line


def _parse_component_deco(python_src: str, comp_name: str, filename: str) -> dict[str, dict]:
    """Read props={...} from the @component decorator for a component def."""
    try:
        tree = pyast.parse(python_src)
    except SyntaxError:
        return {}
    for node in tree.body:
        if isinstance(node, (pyast.FunctionDef, pyast.AsyncFunctionDef)) and node.name == comp_name:
            for deco in node.decorator_list:
                if isinstance(deco, pyast.Call) and getattr(deco.func, "id", "") == "component":
                    for kw in deco.keywords:
                        if kw.arg == "props" and isinstance(kw.value, pyast.Dict):
                            out: dict[str, dict] = {}
                            for k, v in zip(kw.value.keys, kw.value.values):
                                if not isinstance(k, pyast.Constant) or not isinstance(k.value, str):
                                    raise CompileError(
                                        f"component '{comp_name}': prop names must be string literals",
                                        Span(
                                            file=filename,
                                            start_line=getattr(k, "lineno", 1),
                                            start_col=0,
                                            snippet="",
                                        ),
                                        hint='Use props={"title": str, "count": 0}.',
                                    )
                                required = isinstance(v, pyast.Name)
                                default = None if required else pyast.unparse(v)
                                out[k.value] = {
                                    "required": required,
                                    "default": default,
                                    "line": getattr(v, "lineno", 1),
                                }
                            return out
                    return {}
    return {}


def _find_signal_names(python_src: str) -> set[str]:
    try:
        tree = pyast.parse(python_src)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in pyast.walk(tree):
        if isinstance(node, pyast.Call) and getattr(node.func, "id", "") in ("signal", "computed"):
            parent = node
            # handled below via assignment scan
            _ = parent
    # scan assignments: x = signal(...) / x: T = signal(...)
    for node in tree.body:
        for sub in pyast.walk(node):
            if isinstance(sub, pyast.Assign):
                if isinstance(sub.value, pyast.Call) and getattr(sub.value.func, "id", "") in (
                    "signal",
                    "computed",
                ):
                    for t in sub.targets:
                        if isinstance(t, pyast.Name):
                            names.add(t.id)
            elif isinstance(sub, pyast.AnnAssign):
                if isinstance(sub.value, pyast.Call) and getattr(sub.value.func, "id", "") in (
                    "signal",
                    "computed",
                ):
                    if isinstance(sub.target, pyast.Name):
                        names.add(sub.target.id)
    # signals can also be defined inside page/component functions
    return names


class Analysis:
    def __init__(
        self,
        signals: set[str],
        handlers: set[str],
        components: dict[str, ComponentDecl],
        routes: dict[str, str],
    ):
        self.signals = signals
        self.handlers = handlers
        self.components = components
        self.routes = routes  # route path -> root name


class Analyzer:
    def __init__(self, doc: Document):
        self.doc = doc

    def run(self) -> Analysis:
        signals = _find_signal_names(self.doc.python_src)
        handlers, _sigs = collect_handlers(self.doc.python_src)
        components: dict[str, ComponentDecl] = {}
        routes: dict[str, str] = {}
        # discover routes + component prop schemas from python source
        try:
            tree = pyast.parse(self.doc.python_src)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in tree.body:
                if not isinstance(node, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
                    continue
                for deco in node.decorator_list:
                    if not isinstance(deco, pyast.Call):
                        continue
                    fname = getattr(deco.func, "id", "")
                    if fname == "page" and deco.args and isinstance(deco.args[0], pyast.Constant):
                        routes[str(deco.args[0].value)] = node.name
                    elif fname == "component":
                        props = _parse_component_deco(self.doc.python_src, node.name, self.doc.filename)
                        components[node.name] = ComponentDecl(node.name, props, node.lineno)
        # every component root in markup must have a python def; every def used
        # in markup must exist
        root_names = {r.name for r in self.doc.roots}
        for cname in list(components):
            if cname not in root_names:
                pass  # pure-python component: allowed
        for root in self.doc.roots:
            if root.kind == "component" and root.name not in components:
                # component without decorator props: default to no declared props
                components[root.name] = ComponentDecl(root.name, {}, root.span.start_line)
        # validate each root
        for root in self.doc.roots:
            scope = set(signals)
            if root.kind == "component" and root.name in components:
                scope |= set(components[root.name].props)
            # for-loop vars add to scope as we descend
            self._check_nodes(root.nodes, scope, handlers, components, in_component=root.kind == "component")
        return Analysis(signals=signals, handlers=handlers, components=components, routes=routes)

    # -- checking ---------------------------------------------------------
    def _check_nodes(self, nodes, scope, handlers, components, in_component, loop_vars=frozenset()):
        for node in nodes:
            self._check_node(node, scope, handlers, components, in_component, loop_vars)

    def _check_node(self, node, scope, handlers, components, in_component, loop_vars):
        if isinstance(node, Text):
            return
        if isinstance(node, Style):
            return
        if isinstance(node, DynText):
            validate_expr(node.code, self.doc.filename, node.line, "text interpolation")
            return
        if isinstance(node, Element):
            from pyweb.compiler.ast import Slot as _Slot

            for attr in node.attrs:
                self._check_attr(attr, node.tag, scope, handlers)
            # unknown-component check: lowercase unknown tags are fine (HTML)
            self._check_nodes(node.children, scope, handlers, components, in_component, loop_vars)
            return
        if isinstance(node, For):
            validate_expr(node.iter_code, self.doc.filename, node.iter_line, "'for' iterable")
            if node.key_code:
                validate_expr(node.key_code, self.doc.filename, node.span.start_line, "key=")
            inner = set(loop_vars) | {node.var}
            self._check_nodes(node.body, scope | {node.var}, handlers, components, in_component, inner)
            # key required only when iterating reactive state with >1 dynamic row;
            # default (index) is allowed, so no error here.
            return
        if isinstance(node, Cond):
            for branch in node.branches:
                if branch.cond_code is not None:
                    validate_expr(branch.cond_code, self.doc.filename, branch.cond_line, "'if' condition")
                self._check_nodes(branch.body, scope, handlers, components, in_component, loop_vars)
            return
        if isinstance(node, CompUse):
            decl = components.get(node.name)
            if decl is None and not self._is_pure_python_component(node.name):
                raise CompileError(
                    f"unknown component '<{node.name}>'",
                    node.span,
                    hint=f"Define it with @component(props={{...}}) def {node.name}(...):.",
                )
            if decl is not None:
                given = set(node.props)
                required = {p for p, s in decl.props.items() if s["required"]}
                missing = required - given
                if missing:
                    raise CompileError(
                        f"component '<{node.name}>' is missing required prop(s): {', '.join(sorted(missing))}",
                        node.span,
                        hint=f"Add e.g. {sorted(missing)[0]}={{...}} to the '<{node.name}>' tag.",
                    )
                extra = given - set(decl.props)
                # children/slots are not props
                if extra:
                    raise CompileError(
                        f"component '<{node.name}>' got unexpected prop(s): {', '.join(sorted(extra))}",
                        node.span,
                        hint=f"Declared props are: {', '.join(sorted(decl.props)) or '(none)'}.",
                    )
                for attr in node.props.values():
                    self._check_attr(attr, node.name, scope | set(decl.props), handlers)
            else:
                for attr in node.props.values():
                    self._check_attr(attr, node.name, scope, handlers)
            self._check_nodes(node.children, scope, handlers, components, in_component, loop_vars)
            return
        # Slot
        return

    def _is_pure_python_component(self, name: str) -> bool:
        try:
            tree = pyast.parse(self.doc.python_src)
        except SyntaxError:
            return False
        names = {
            n.name
            for n in tree.body
            if isinstance(n, (pyast.FunctionDef, pyast.AsyncFunctionDef, pyast.ClassDef))
        }
        return name in names

    def _check_attr(self, attr: Attr, tag: str, scope: set[str], handlers: set[str]) -> None:
        from pyweb.compiler.ast import BindRef, CssDyn, CssStatic, Dyn, HandlerRef, Static

        v = attr.value
        if isinstance(v, BindRef):
            if v.name not in scope and v.name not in self.doc.python_src:
                raise CompileError(
                    f"bind={{{v.name}}} refers to unknown signal '{v.name}'",
                    attr.span,
                    hint=f"Define '{v.name} = signal(...)' before the markup.",
                )
            return
        if isinstance(v, HandlerRef):
            if v.name not in handlers:
                raise CompileError(
                    f"{attr.name}={{{v.name}}} refers to unknown handler '{v.name}'",
                    attr.span,
                    hint=f"Define 'def {v.name}(...):' at module level.",
                )
            return
        if isinstance(v, Dyn):
            validate_expr(v.code, self.doc.filename, v.line, f"attribute '{attr.name}'")
            return
        if isinstance(v, CssDyn):
            validate_expr(v.code, self.doc.filename, v.line, "css={...}")
            return
        if isinstance(v, (Static, CssStatic)):
            return
