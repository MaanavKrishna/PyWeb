"""Placement pass: decide SSR-static vs live-hydrated subtrees.

Marks every node with ``.live`` (needs client hydration) and ``.hid``
(hydration id, already assigned by the parser). A node is live when it or any
descendant binds reactive state: DynText, bind=, dynamic handlers, reactive
for-iterables, reactive if-conditions, or component children thereof.
"""
from __future__ import annotations

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
    Style,
    Text,
)
from pyweb.compiler.reactivity import is_probably_reactive


class Placement:
    def __init__(self, signals: set[str], handlers: set[str], components: dict):
        self.signals = signals
        self.handlers = handlers
        self.components = components

    def run(self, nodes: list) -> bool:
        live = False
        for node in nodes:
            if self.visit(node):
                live = True
        return live

    def visit(self, node) -> bool:
        if isinstance(node, Text):
            node.live = False
            return False
        if isinstance(node, DynText):
            node.live = True
            return True
        if isinstance(node, Style):
            return False
        if isinstance(node, Slot):
            return False
        if isinstance(node, Element):
            return self.visit_element(node)
        if isinstance(node, For):
            body_live = self.run(node.body)
            iter_live = is_probably_reactive(node.iter_code, self.signals)
            node.live = iter_live or body_live
            if node.live and not iter_live:
                # body reactivity over a static list still needs hydration
                # for per-row dynamic bits, but renders statically first.
                pass
            return node.live
        if isinstance(node, Cond):
            from pyweb.compiler.reactivity import validate_expr  # noqa: F401
            any_live = False
            for branch in node.branches:
                child_live = self.run(branch.body)
                cond_live = (
                    branch.cond_code is not None
                    and is_probably_reactive(branch.cond_code, self.signals)
                )
                any_live = any_live or child_live or cond_live
            # A conditional block is live iff its condition is reactive.
            conds_live = any(
                b.cond_code is not None and is_probably_reactive(b.cond_code, self.signals)
                for b in node.branches
            )
            node.live = conds_live or any_live
            return node.live
        if isinstance(node, CompUse):
            child_live = self.run(node.children)
            props_live = any(
                isinstance(a.value, (Dyn, CssDyn)) or isinstance(a.value, (BindRef, HandlerRef))
                for a in node.props.values()
            )
            return child_live or props_live
        return False

    def visit_element(self, node: Element) -> bool:
        child_live = self.run(node.children)
        attr_live = False
        for attr in node.attrs:
            v = attr.value
            if isinstance(v, BindRef):
                attr_live = True
            elif isinstance(v, (Dyn, CssDyn)):
                attr_live = True
            elif isinstance(v, HandlerRef):
                # static handler refs still need hydration for events
                attr_live = True
        node.live = child_live or attr_live
        return node.live
