"""JS emitter: reactive page → minimal fine-grained browser module."""

from __future__ import annotations

import json

from ..rpc import client_stub

RUNTIME_IMPORT = "import { sig, computed, bind_text, bind_input, on, rpc, mutate, liveList, liveIf, takeover, dynText } from './runtime.js';\n"


def _js_val(v):
    return json.dumps(v)


def emit_js(page_name, ui, signals, computeds, initial, rpc_specs, handlers=None, sourcemap_out=None):
    """Generate a JS module. Dynamic {expr} → bound text nodes (no VDOM)."""
    handlers = handlers or {}
    lines = []
    has_dynamic = bool(signals or computeds or _has_expr(ui) or rpc_specs or handlers)
    if has_dynamic:
        lines.append(RUNTIME_IMPORT)
    for s in signals:
        init = initial.get(s)
        lines.append(f"export const {s} = sig({_js_val(init)}); // pyweb:{s}")
        if sourcemap_out is not None:
            sourcemap_out.append((f"sig {s}", 0))
    emitted_consts: set = set()
    for name, c in computeds.items():
        for dep in c["deps"]:
            if dep not in signals and dep not in emitted_consts and dep in (initial or {}):
                emitted_consts.add(dep)
                lines.append(f"const {dep} = {_js_val(initial[dep])}; // pyweb:const")
        lines.append(f"export const {name} = computed(() => ({_py2js(c['code'])})); // deps {','.join(c['deps'])}")
    for hname, h in handlers.items():
        lines.append(f"export function {hname}(e) {{ {_py2js(h['body_js'])} }} // pyweb-line:{h['line']}")
        if sourcemap_out is not None:
            sourcemap_out.append((f"handler {hname}", h["line"]))
    for spec in rpc_specs:
        lines.append(client_stub(spec) + f" // rpc:{spec['name']} pyweb-line:{spec['line']}")
    scope_names = sorted(set(signals) | set(computeds) | set(handlers))
    if scope_names:
        lines.append(f"window.__pyweb_scope = {{ {', '.join(scope_names)} }};")
    _assign_ids(ui)
    lines.append("export function mount(root=document) {")
    lines.append(_emit_bindings(ui, "root", signals))
    lines.append("}")
    if has_dynamic:
        lines.append("if (typeof document !== 'undefined') {")
        lines.append("  if (document.readyState === 'loading') {")
        lines.append("    document.addEventListener('DOMContentLoaded', () => mount(document));")
        lines.append("  } else { mount(document); }")
        lines.append("}")
    return "\n".join(lines) + "\n"


def _has_expr(ui):
    for n in ui:
        t = type(n).__name__
        if t == "ExprNode":
            return True
        if t == "Element" and any(isinstance(v, tuple) for v in n.attrs.values()):
            return True
        kids = getattr(n, "children", None) or getattr(n, "body", None) or []
        if kids and _has_expr(kids):
            return True
    return False


def _assign_ids(ui):
    """Walk UI in document order; tag interactive elements with stable data-pw-id."""
    counter = [0]

    def walk(nodes):
        for n in nodes:
            t = type(n).__name__
            if t == "Element":
                interactive = any(
                    (key.startswith("on") or key == "bind") and isinstance(val, tuple)
                    for key, val in n.attrs.items())
                if interactive:
                    counter[0] += 1
                    n.pw_id = f"pw{counter[0]:05d}"
                walk(n.children)
            elif t in ("ControlFor", "ControlIf", "_ControlBox"):
                walk(getattr(n, "body", []) or [])
                for c in getattr(n, "orelse", []):
                    walk([c])

    walk(ui)


def _emit_bindings(ui, root_var, signals=()):
    out = []
    for n in ui:
        t = type(n).__name__
        if t == "ExprNode":
            out.append(f"  bind_text({root_var}, {json.dumps(n.code)}, () => ({_py2js(n.code, signals)})); // line {n.line}")
        elif t == "Element":
            el_id = getattr(n, "pw_id", None)
            for key, val in n.attrs.items():
                if key.startswith("on") and isinstance(val, tuple):
                    fn = val[1]
                    if el_id:
                        out.append(f"  on({root_var}, '{key[2:]}', {fn}, {el_id!r}); // line {val[2]}")
                    else:
                        out.append(f"  on({root_var}, '{key[2:]}', {fn}); // line {val[2]}")
                elif key == "bind" and isinstance(val, tuple):
                    if el_id:
                        out.append(f"  bind_input({root_var}, {json.dumps(val[1])}, {el_id!r}); // line {val[2]}")
                    else:
                        out.append(f"  bind_input({root_var}, {json.dumps(val[1])}); // line {val[2]}")
            if n.children:
                out.append(_emit_bindings(n.children, root_var, signals))
        elif t == "ControlFor":
            out.append(_emit_live_for(n, root_var, signals))
        elif t in ("ControlIf", "_ControlBox"):
            body = getattr(n, "body", [])
            if body:
                out.append(_emit_bindings(body, root_var, signals))
            for c in getattr(n, "orelse", []):
                out.append(_emit_bindings([c], root_var, signals))
    return "\n".join(out) if out else "  // static — no bindings"


_live_counter = [0]


def _emit_live_for(n, root_var, signals=()):
    """Emit a keyed liveList region for `for x in items` (Track A port).

    SSR keeps the initial rows as static HTML; the browser takes over the
    region and patches rows fine-grained as the signal changes.
    """
    _live_counter[0] += 1
    hid = _live_counter[0]
    target, iterable = n.target, n.iterable
    iter_js = _py2js(iterable, signals)
    body = _emit_bindings(getattr(n, "body", []), "__frag", signals)
    key_fn = f"(({target}) => ({_py2js(target, signals)}))"
    return (
        f"  // live-for hid {hid} (line {n.line}): SSR rows activate below\n"
        f"  (() => {{ const __anchor = takeover({root_var}, {hid});\n"
        f"    const __row = ({target}, __i, __key) => {{\n"
        f"      const __frag = document.createDocumentFragment();\n"
        f"{body}\n"
        f"      return {{ els: Array.from(__frag.childNodes) }}; }};\n"
        f"    liveList(__anchor, () => ({iter_js}), "
        f"(__item, __i, __key) => __row(__item, __i, __key), {key_fn}); }})();"
    )


def _py2js(code, signals=()):
    # Tiny expression lowering for the prototype: Python → JS for common ops.
    # Signals are callable refs: `count` reads as `count()`, `count += 1`
    # becomes `count(count() + 1)`. Full typing lives in PIR long-term.
    import re
    for s in sorted(signals, key=len, reverse=True):
        code = re.sub(rf"\b{re.escape(s)}\b\s*\+=\s*([^;]+)", rf"{s}({s}() + \1)", code)
        code = re.sub(rf"\b{re.escape(s)}\b\s*=\s*(?![=>])([^;]+)", rf"{s}(\1)", code)
        code = re.sub(rf"(?<![\w$.]){re.escape(s)}(?![\w$(\[])", f"{s}()", code)
    code = re.sub(r"\bTrue\b", "true", code)
    code = re.sub(r"\bFalse\b", "false", code)
    code = re.sub(r"\bNone\b", "null", code)
    code = re.sub(r"\band\b", "&&", code)
    code = re.sub(r"\bor\b", "||", code)
    code = re.sub(r"\bnot\b", "!", code)
    return code
