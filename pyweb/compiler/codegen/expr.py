"""Lower a subset of Python expressions to JavaScript."""
from __future__ import annotations

import ast as pyast


class LowerError(Exception):
    pass


def to_js(
    code: str,
    scope: set[str],
    signals: set[str],
    loop_vars: set[str],
    renames: dict[str, str] | None = None,
) -> str:
    """Lower ``code`` to JS. Signal reads become ``sig.get()``; loop vars
    stay bare; ``renames`` maps source names (component props) to JS ids."""
    try:
        tree = pyast.parse(code, mode="eval")
        node = tree.body
    except SyntaxError as e:
        raise LowerError(f"cannot lower expression {code!r}: {e.msg}")
    return _lower(node, scope, signals, loop_vars, renames or {})


def _lower(
    node: pyast.AST,
    scope: set[str],
    signals: set[str],
    loop_vars: set[str],
    renames: dict[str, str],
) -> str:
    if isinstance(node, pyast.Constant):
        if node.value is True:
            return "true"
        if node.value is False:
            return "false"
        if node.value is None:
            return "null"
        return repr(node.value)
    if isinstance(node, pyast.Name):
        if node.id in renames:
            return renames[node.id]
        if node.id in loop_vars:
            return node.id
        if node.id in signals:
            return f"{node.id}.get()"
        return node.id
    if isinstance(node, pyast.Attribute):
        base = _lower(node.value, scope, signals, loop_vars, renames)
        # loop-var attribute stays on the loop var, e.g. item.text
        return f"{base}.{node.attr}"
    if isinstance(node, pyast.Call):
        fn = _lower(node.func, scope, signals, loop_vars, renames)
        args = ", ".join(_lower(a, scope, signals, loop_vars, renames) for a in node.args)
        return f"{fn}({args})"
    if isinstance(node, pyast.BinOp):
        l = _lower(node.left, scope, signals, loop_vars, renames)
        r = _lower(node.right, scope, signals, loop_vars, renames)
        op = _binop(node.op)
        if isinstance(node.op, pyast.Mod) and _is_str(node.left):
            return f"{l}.replace(/%s/g, {r})"
        return f"({l} {op} {r})"
    if isinstance(node, pyast.BoolOp):
        op = "&&" if isinstance(node.op, pyast.And) else "||"
        return "(" + f" {op} ".join(_lower(v, scope, signals, loop_vars, renames) for v in node.values) + ")"
    if isinstance(node, pyast.UnaryOp):
        v = _lower(node.operand, scope, signals, loop_vars, renames)
        if isinstance(node.op, pyast.Not):
            return f"(!{v})"
        if isinstance(node.op, pyast.USub):
            return f"(-{v})"
        if isinstance(node.op, pyast.UAdd):
            return f"(+{v})"
        raise LowerError("unsupported unary op")
    if isinstance(node, pyast.Compare):
        l = _lower(node.left, scope, signals, loop_vars, renames)
        parts = []
        for op, comp in zip(node.ops, node.comparators):
            r = _lower(comp, scope, signals, loop_vars, renames)
            parts.append(f"({l} {_cmpop(op)} {r})")
            l = r
        return " && ".join(parts)
    if isinstance(node, pyast.IfExp):
        return (
            f"({_lower(node.test, scope, signals, loop_vars, renames)} ? "
            f"{_lower(node.body, scope, signals, loop_vars, renames)} : "
            f"{_lower(node.orelse, scope, signals, loop_vars, renames)})"
        )
    if isinstance(node, pyast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, pyast.Constant):
                parts.append(_quote(str(v.value)))
            else:
                parts.append("(${%s})" % _lower(v.value, scope, signals, loop_vars, renames))
        return "`" + "".join(parts).replace("${", "${") + "`"
    if isinstance(node, pyast.FormattedValue):
        inner = _lower(node.value, scope, signals, loop_vars, renames)
        return "${" + inner + "}"
    if isinstance(node, pyast.Subscript):
        base = _lower(node.value, scope, signals, loop_vars, renames)
        sl = node.slice
        if isinstance(sl, pyast.Constant) and isinstance(sl.value, str):
            return f"{base}[{_quote(sl.value)}]"
        return f"{base}[{_lower(sl, scope, signals, loop_vars, renames)}]"
    if isinstance(node, pyast.List):
        return "[" + ", ".join(_lower(e, scope, signals, loop_vars, renames) for e in node.elts) + "]"
    if isinstance(node, pyast.Tuple):
        return "[" + ", ".join(_lower(e, scope, signals, loop_vars, renames) for e in node.elts) + "]"
    if isinstance(node, pyast.Dict):
        items = []
        for k, v in zip(node.keys, node.values):
            js_v = _lower(v, scope, signals, loop_vars, renames)
            if k is None:
                items.append(f"...{js_v}")
            elif isinstance(k, pyast.Constant) and isinstance(k.value, str):
                items.append(f"{_quote(k.value)}: {js_v}")
            else:
                items.append(f"[{_lower(k, scope, signals, loop_vars, renames)}]: {js_v}")
        return "({" + ", ".join(items) + "})"
    if isinstance(node, pyast.ListComp):
        return _lower_comp(node, scope, signals, loop_vars, renames)
    if isinstance(node, pyast.GeneratorExp):
        return _lower_comp(node, scope, signals, loop_vars, renames)
    raise LowerError(f"unsupported expression: {pyast.dump(node)}")


def _lower_comp(node, scope, signals, loop_vars, renames) -> str:
    elt = node.elt
    gens = node.generators
    if len(gens) != 1:
        raise LowerError("only single-generator comprehensions are supported")
    gen = gens[0]
    if gen.ifs or gen.is_async:
        raise LowerError("filtered/async comprehensions are not supported in markup")
    if not isinstance(gen.target, pyast.Name):
        raise LowerError("destructuring in comprehensions is not supported in markup")
    inner_vars = set(loop_vars) | {gen.target.id}
    src = _lower(gen.iter, scope, signals, loop_vars, renames)
    body = _lower(elt, scope, signals, inner_vars, renames)
    return f"{src}.map(({gen.target.id}) => {body})"


def _binop(op) -> str:
    if isinstance(op, pyast.Add):
        return "+"
    if isinstance(op, pyast.Sub):
        return "-"
    if isinstance(op, pyast.Mult):
        return "*"
    if isinstance(op, pyast.Div):
        return "/"
    if isinstance(op, pyast.Mod):
        return "%"
    if isinstance(op, pyast.Pow):
        return "**"
    if isinstance(op, pyast.FloorDiv):
        raise LowerError("use / instead of // in markup expressions")
    raise LowerError("unsupported binary operator")


def _cmpop(op) -> str:
    if isinstance(op, (pyast.Eq, pyast.Is)):
        return "==="
    if isinstance(op, (pyast.NotEq, pyast.IsNot)):
        return "!=="
    if isinstance(op, pyast.Lt):
        return "<"
    if isinstance(op, pyast.LtE):
        return "<="
    if isinstance(op, pyast.Gt):
        return ">"
    if isinstance(op, pyast.GtE):
        return ">="
    if isinstance(op, pyast.In):
        raise LowerError("'in' is not supported in markup expressions; use a helper")
    if isinstance(op, pyast.NotIn):
        raise LowerError("'not in' is not supported in markup expressions")
    raise LowerError("unsupported comparison")


def _is_str(node: pyast.AST) -> bool:
    return isinstance(node, pyast.Constant) and isinstance(node.value, str)


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
