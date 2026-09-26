"""SSR renderer: UI AST -> static HTML with hydration markers."""
from __future__ import annotations

import html as _html
import json

from pyweb.compiler.ast import (
    Attr,
    BindRef,
    CompUse,
    Cond,
    CssDyn,
    CssStatic,
    Dyn,
    DynText,
    Element,
    For,
    HandlerRef,
    Slot,
    Static,
    Text,
)
from pyweb.compiler.codegen import expr as lowering
from pyweb.reactive import Computed, Signal


class SSR:
    """Render static HTML. Needs ``values`` (signal name -> python value) and
    ``components`` to inline component templates."""

    def __init__(
        self,
        analysis,
        values: dict | None = None,
        components: dict[str, list] | None = None,
        filename: str = "<input>",
    ):
        self.a = analysis
        self.values = values or {}
        self.templates = components or {}
        self.filename = filename

    # -- values -----------------------------------------------------------
    def eval_static(self, code: str, line: int, loop_env: dict | None = None):
        import ast as pyast

        try:
            tree = pyast.parse(code, mode="eval")
        except SyntaxError:
            return None
        env = dict(self.values)
        if loop_env:
            env.update(loop_env)
        try:
            return self._eval(tree.body, env)
        except Exception:
            return None

    def _eval(self, node, env: dict):
        import ast as pyast

        if isinstance(node, pyast.Constant):
            return node.value
        if isinstance(node, pyast.Name):
            v = env.get(node.id)
            if isinstance(v, (Signal, Computed)):
                return v.get()
            return v
        if isinstance(node, pyast.Attribute):
            base = self._eval(node.value, env)
            if isinstance(base, dict):
                return base.get(node.attr)
            return getattr(base, node.attr, None)
        if isinstance(node, pyast.BinOp):
            l = self._eval(node.left, env)
            r = self._eval(node.right, env)
            if isinstance(node.op, pyast.Add):
                return l + r
            if isinstance(node.op, pyast.Sub):
                return l - r
            if isinstance(node.op, pyast.Mult):
                return l * r
            if isinstance(node.op, pyast.Div):
                return l / r
            if isinstance(node.op, pyast.Mod):
                return l % r
            raise ValueError("op")
        if isinstance(node, pyast.BoolOp):
            vals = [self._eval(v, env) for v in node.values]
            if isinstance(node.op, pyast.And):
                out = True
                for v in vals:
                    out = out and v
                return out
            out = False
            for v in vals:
                out = out or v
            return out
        if isinstance(node, pyast.UnaryOp) and isinstance(node.op, pyast.Not):
            return not self._eval(node.operand, env)
        if isinstance(node, pyast.Compare):
            l = self._eval(node.left, env)
            for op, comp in zip(node.ops, node.comparators):
                r = self._eval(comp, env)
                ok = False
                if isinstance(op, (pyast.Eq, pyast.Is)):
                    ok = l == r
                elif isinstance(op, (pyast.NotEq, pyast.IsNot)):
                    ok = l != r
                elif isinstance(op, pyast.Lt):
                    ok = l < r
                elif isinstance(op, pyast.LtE):
                    ok = l <= r
                elif isinstance(op, pyast.Gt):
                    ok = l > r
                elif isinstance(op, pyast.GtE):
                    ok = l >= r
                else:
                    raise ValueError("cmp")
                if not ok:
                    return False
                l = r
            return True
        if isinstance(node, pyast.IfExp):
            return self._eval(node.body, env) if self._eval(node.test, env) else self._eval(node.orelse, env)
        if isinstance(node, pyast.JoinedStr):
            out = []
            for v in node.values:
                if isinstance(v, pyast.Constant):
                    out.append(str(v.value))
                else:
                    out.append(str(self._eval(v.value, env)))
            return "".join(out)
        if isinstance(node, pyast.FormattedValue):
            return self._eval(node.value, env)
        if isinstance(node, pyast.Subscript):
            base = self._eval(node.value, env)
            idx = self._eval(node.slice, env)
            return base[idx]
        if isinstance(node, pyast.List):
            return [self._eval(e, env) for e in node.elts]
        if isinstance(node, pyast.Tuple):
            return tuple(self._eval(e, env) for e in node.elts)
        if isinstance(node, pyast.Dict):
            return {
                (self._eval(k, env) if k is not None else None): self._eval(v, env)
                for k, v in zip(node.keys, node.values)
            }
        raise ValueError(f"non-static: {pyast.dump(node)}")

    # -- render -------------------------------------------------------------
    def render(self, nodes: list, loop_env: dict | None = None) -> str:
        return "".join(self.render_node(n, loop_env or {}) for n in nodes)

    def render_node(self, node, loop_env: dict) -> str:
        if isinstance(node, Text):
            return _html.escape(node.content)
        if isinstance(node, DynText):
            v = self.eval_static(node.code, node.line, loop_env)
            inner = "" if v is None else _html.escape(str(v))
            return f"<!--pw:{node.hid}-->{inner}<!--/pw:{node.hid}-->"
        if isinstance(node, Element):
            return self.render_element(node, loop_env)
        if isinstance(node, For):
            return self.render_for(node, loop_env)
        if isinstance(node, Cond):
            return self.render_cond(node, loop_env)
        if isinstance(node, CompUse):
            return self.render_comp(node, loop_env)
        if isinstance(node, Slot):
            return f"<!--slot:{node.name}-->"
        return ""

    def render_element(self, node: Element, loop_env: dict) -> str:
        if node.tag == "<>":
            return self.render(node.children, loop_env)
        attrs = self.render_attrs(node, loop_env)
        cls = f' class="{node.css_class}"' if node.css_class else ""
        hid = f' data-pw-hid="{node.hid}"' if self._needs_hid(node) else ""
        inner = self.render(node.children, loop_env)
        if node.tag in ("input", "img", "br", "hr", "meta", "link"):
            return f"<{node.tag}{cls}{hid}{attrs}>"
        return f"<{node.tag}{cls}{hid}{attrs}>{inner}</{node.tag}>"

    @staticmethod
    def _needs_hid(node: Element) -> bool:
        for attr in node.attrs:
            if isinstance(attr.value, (Dyn, HandlerRef, BindRef, CssDyn, CssStatic)):
                return True
        return False

    def render_attrs(self, node: Element, loop_env: dict) -> str:
        out = []
        for attr in node.attrs:
            v = attr.value
            if isinstance(v, HandlerRef):
                out.append(f' data-on{attr.name[2:].lower()}="{v.name}"')
            elif isinstance(v, BindRef):
                cur = self.values.get(v.name)
                if isinstance(cur, (Signal, Computed)):
                    cur = cur.get()
                if attr.name in ("value", "checked") and cur is not None:
                    out.append(f' {attr.name}="{_html.escape(str(cur), quote=True)}"')
                else:
                    out.append(f' data-bind="{v.name}"')
            elif isinstance(v, Dyn):
                sv = self.eval_static(v.code, v.line, loop_env)
                if sv is None:
                    out.append(f"<!--pw-attr:{node.hid}:{attr.name}-->")
                else:
                    out.append(f' {attr.name}="{_html.escape(str(sv), quote=True)}"')
            elif isinstance(v, (CssDyn,)):
                sv = self.eval_static(v.code, v.line, loop_env)
                if sv is not None:
                    out.append(f' style="{_html.escape(str(sv), quote=True)}"')
            elif isinstance(v, CssStatic):
                out.append(f' style="{_html.escape(v.css, quote=True)}"')
            elif isinstance(v, Static):
                if v.text == "" and attr.name not in ("alt", "value", "placeholder"):
                    out.append(f" {attr.name}")
                else:
                    out.append(f' {attr.name}="{_html.escape(v.text, quote=True)}"')
        return "".join(out)

    def render_for(self, node: For, loop_env: dict) -> str:
        items = self.eval_static(node.iter_code, node.iter_line, loop_env)
        open_m = f"<!--pw:{node.hid}-->"
        close_m = f"<!--/pw:{node.hid}-->"
        if not isinstance(items, (list, tuple)):
            return open_m + close_m
        out = [open_m]
        for item in items:
            env = dict(loop_env)
            env[node.var] = item
            out.append(self.render(node.body, env))
        out.append(close_m)
        return "".join(out)

    def render_cond(self, node: Cond, loop_env: dict) -> str:
        open_m = f"<!--pw:{node.hid}-->"
        close_m = f"<!--/pw:{node.hid}-->"
        for branch in node.branches:
            if branch.cond_code is None:
                return open_m + self.render(branch.body, loop_env) + close_m
            v = self.eval_static(branch.cond_code, branch.cond_line, loop_env)
            if v:
                return open_m + self.render(branch.body, loop_env) + close_m
        return open_m + close_m

    def render_comp(self, node: CompUse, loop_env: dict) -> str:
        tmpl = self.templates.get(node.name)
        if tmpl is None:
            # pure-python component or placeholder
            return f"<!--comp:{node.name}-->" + self.render(node.children, loop_env)
        # resolve props (static where possible), expose as loop env for template
        env = dict(loop_env)
        for pname, attr in node.props.items():
            v = attr.value
            if isinstance(v, Static):
                env[pname] = v.text
            elif isinstance(v, (Dyn, CssDyn)):
                env[pname] = self.eval_static(v.code, v.line, loop_env)
            elif isinstance(v, BindRef):
                cur = self.values.get(v.name)
                env[pname] = cur.get() if isinstance(cur, (Signal, Computed)) else cur
            elif isinstance(v, HandlerRef):
                env[pname] = v.name
        if self.a is not None:
            decl = self.a.components.get(node.name)
            if decl is not None:
                for pname, spec in decl.props.items():
                    if pname not in env and spec.get("default") is not None:
                        try:
                            env[pname] = self.eval_static(spec["default"], node.span.start_line, loop_env)
                        except Exception:
                            env[pname] = None
        child_html = self.render(node.children, loop_env)
        # <slot> substitution
        saved = self.render(node.children, loop_env)
        html = self.render(tmpl, env)
        html = html.replace("<!--slot:default-->", child_html)
        return html

    def page(self, title: str, body_html: str, js_file: str, css_file: str | None, root_nodes=None) -> str:
        css_link = f'\n<link rel="stylesheet" href="{css_file}">' if css_file else ""
        return (
            "<!DOCTYPE html>\n<html>\n<head>\n<meta charset=\"utf-8\">\n"
            f"<title>{_html.escape(title)}</title>{css_link}\n</head>\n"
            f"<body>\n<div id=\"app\">{body_html}</div>\n"
            f"<script src=\"runtime.js\"></script>\n"
            f"<script src=\"{js_file}\"></script>\n</body>\n</html>\n"
        )
