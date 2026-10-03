"""Python → JavaScript translation for browser-placed code.

PyWeb compiles a *defined subset* of Python to JavaScript: the code in
event handlers, `{expressions}` in markup, derived values and module-level
helper functions that browser code calls. Anything outside the subset is
a :class:`CompileError` with the `.pyweb` line, never silently wrong JS.

Python semantics are kept where JavaScript differs (truthiness of empty
lists, ``==`` on lists/dicts, negative indexing, ``//`` and ``%``, string
and list methods, ``KeyError``/``IndexError``) through small helpers in
the browser runtime (``$py.*``).

State is reactive: reading a page signal emits ``name()``; assigning it
emits ``name(value)``; mutating it in place (``todos.append(x)``,
``todos[i]["done"] = True``) becomes a copy-on-write update so every
dependent DOM node refreshes.
"""

from __future__ import annotations

import ast
import json
import math

from .errors import CompileError

#: Python builtins available in browser code (→ ``$py.<name>``).
BUILTINS = frozenset({
    "len", "str", "int", "float", "bool", "abs", "min", "max", "sum", "round",
    "range", "sorted", "reversed", "enumerate", "zip", "list", "dict", "set",
    "tuple", "any", "all", "chr", "ord", "isinstance", "print", "repr",
})

#: Real JavaScript globals usable directly from browser code.
JS_GLOBALS = frozenset({
    "window", "document", "console", "Math", "JSON", "Date", "localStorage",
    "sessionStorage", "navigator", "location", "history", "setTimeout",
    "setInterval", "clearTimeout", "clearInterval", "fetch", "alert", "confirm",
    "prompt", "requestAnimationFrame", "Intl", "URL", "URLSearchParams",
    "Promise", "Number", "String", "Array", "Object", "Event", "CustomEvent",
    "FormData", "Blob", "crypto", "performance", "structuredClone",
})

#: Python methods dispatched through ``$py.m`` (strings, lists, dicts, sets).
PY_METHODS = frozenset({
    "append", "extend", "insert", "pop", "remove", "index", "count", "clear",
    "sort", "reverse", "copy", "get", "keys", "values", "items", "update",
    "setdefault", "add", "discard", "upper", "lower", "strip", "lstrip",
    "rstrip", "split", "splitlines", "join", "replace", "startswith",
    "endswith", "find", "rfind", "title", "capitalize", "isdigit",
    "isnumeric", "isalpha", "isalnum", "isspace", "isupper", "islower",
    "zfill", "ljust", "rjust", "center", "format",
})

#: Methods that mutate their receiver (→ copy-on-write on state).
MUTATING = frozenset({
    "append", "extend", "insert", "pop", "remove", "clear", "sort", "reverse",
    "update", "setdefault", "add", "discard",
})

#: Calls that always produce a boolean (skip the truthiness helper).
BOOL_CALLS = frozenset({"bool", "isinstance", "any", "all", "callable"})
BOOL_METHODS = frozenset({
    "startswith", "endswith", "isdigit", "isnumeric", "isalpha", "isalnum",
    "isspace", "isupper", "islower", "includes",
})

JS_RESERVED = frozenset({
    "arguments", "await", "case", "catch", "const", "debugger", "default",
    "delete", "do", "enum", "eval", "export", "extends", "function",
    "implements", "instanceof", "interface", "let", "new", "package",
    "private", "protected", "public", "static", "super", "switch", "this",
    "throw", "typeof", "var", "void", "undefined", "NaN", "Infinity",
    "yield", "null", "true", "false",
})

# Name kinds in a Scope.
SIGNAL = "signal"          # read: name()      write: name(v)
COMPUTED = "computed"      # read: name()      write: error
PROP = "prop"              # component prop getter: read name()
VALUE = "value"            # plain JS binding (locals, loop vars, params)
CONST = "const"            # read-only plain binding
HANDLER = "handler"        # browser function defined in the page
SERVER = "server"          # @server function: call → await RPC
HELPER = "helper"          # module-level function compiled on demand
MODCONST = "modconst"      # module-level literal constant
COMPONENT = "component"
SERVER_ONLY = "server_only"  # exists only on the server (imports, models, ...)

REACTIVE_KINDS = (SIGNAL, COMPUTED, PROP)


def jsname(name):
    return name + "_" if name in JS_RESERVED else name


class Scope:
    def __init__(self, parent=None, names=None, kind="block"):
        self.parent = parent
        self.names = dict(names or {})
        self.kind = kind

    def lookup(self, name):
        s = self
        while s is not None:
            if name in s.names:
                return s.names[name]
            s = s.parent
        return None

    def child(self, names=None, kind="block"):
        return Scope(self, names, kind)


class ModuleContext:
    """Module-level facts the translator needs (filled by the lowering pass)."""

    def __init__(self, filename="<pyweb>"):
        self.filename = filename
        self.app_config = {}       # literal App(...) keyword arguments
        self.server_fns = {}       # name -> [param names]
        self.helpers = {}          # name -> FunctionDef
        self.modconsts = {}        # name -> python value
        self.components = {}       # name -> component info (local or imported)
        self.imported_components = {}  # local name -> (library, name in that file)
        self.libraries = []        # other .pyweb files imported directly
        self.server_only = {}      # name -> reason
        self.browser_globals = {}  # name -> js expression (pyweb.browser imports)
        # results of on-demand helper compilation
        self.helper_js = {}        # name -> js source
        self.helper_async = {}     # name -> bool
        self.used_consts = set()
        self.used_helpers = []
        self.rpc_calls = {}        # rpc name -> set of caller names

    def module_scope(self):
        names = {}
        for n in self.server_only:
            names[n] = SERVER_ONLY
        for n in self.modconsts:
            names[n] = MODCONST
        for n in self.helpers:
            names[n] = HELPER
        for n in self.server_fns:
            names[n] = SERVER
        for n in self.components:
            names[n] = COMPONENT
        for n in self.browser_globals:
            names[n] = "browser_global"
        return Scope(None, names, kind="module")


class _Fn:
    """Per-function translation state."""

    def __init__(self, name):
        self.name = name
        self.is_async = False


class Translator:
    def __init__(self, ctx: ModuleContext):
        self.ctx = ctx
        self.fn_stack = []
        self._tmp = 0

    # ------------------------------------------------------------ errors
    def error(self, node, msg):
        return CompileError(msg, getattr(node, "lineno", None), self.ctx.filename)

    def tmp(self, base="t"):
        self._tmp += 1
        return f"${base}{self._tmp}"

    def mark_async(self):
        if self.fn_stack:
            self.fn_stack[-1].is_async = True

    # ------------------------------------------------------------- names
    def read_name(self, node, scope):
        name = node.id
        kind = scope.lookup(name)
        js = jsname(name)
        if kind in REACTIVE_KINDS:
            return f"{js}()"
        if kind in (VALUE, CONST, HANDLER):
            return js
        if kind == MODCONST:
            self.ctx.used_consts.add(name)
            return js
        if kind == HELPER:
            self.compile_helper(name, node)
            return js
        if kind == COMPONENT:
            return js
        if kind == "browser_global":
            return self.ctx.browser_globals[name]
        if kind == SERVER:
            raise self.error(node, f"server function {name!r} must be called, e.g. {name}(...); "
                                   "it cannot be passed around in browser code")
        if kind == SERVER_ONLY:
            raise self.error(node, f"{name!r} only exists on the server "
                                   f"({self.ctx.server_only[name]}). Browser code cannot use it; "
                                   "wrap the work in an @server function and call that instead")
        if name in BUILTINS:
            return f"$py.{name}"
        if name in JS_GLOBALS:
            return name
        raise self.error(node, f"name {name!r} is not defined in browser code")

    def is_reactive(self, node, scope):
        """Does evaluating ``node`` read a signal, computed or prop?"""
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                if scope.lookup(sub.id) in REACTIVE_KINDS:
                    return True
        return False

    # ------------------------------------------------------- expressions
    def expr(self, node, scope):
        m = getattr(self, "x_" + type(node).__name__, None)
        if m is None:
            raise self.error(node, f"{type(node).__name__} expressions are not supported in browser code")
        return m(node, scope)

    def x_Constant(self, node, scope):
        v = node.value
        if v is None:
            return "null"
        if v is True:
            return "true"
        if v is False:
            return "false"
        if isinstance(v, str):
            return json.dumps(v)
        if isinstance(v, int):
            if abs(v) > 2 ** 53:
                raise self.error(node, "integers beyond 2**53 cannot be represented exactly in the browser")
            return str(v)
        if isinstance(v, float):
            if math.isinf(v):
                return "Infinity" if v > 0 else "-Infinity"
            if math.isnan(v):
                return "NaN"
            return repr(v)
        if v is Ellipsis:
            return "null"
        raise self.error(node, f"{type(v).__name__} literals are not supported in browser code")

    def x_Name(self, node, scope):
        if node.id == "self":
            raise self.error(node, "classes are not supported in browser code")
        return self.read_name(node, scope)

    def x_JoinedStr(self, node, scope):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value).replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${"))
            else:
                parts.append("${" + self.formatted(v, scope) + "}")
        return "`" + "".join(parts) + "`"

    def formatted(self, v, scope):
        inner = self.expr(v.value, scope)
        if v.conversion == ord("r"):
            inner = f"$py.repr({inner})"
        if v.format_spec is not None:
            spec = v.format_spec
            if (isinstance(spec, ast.JoinedStr) and len(spec.values) == 1
                    and isinstance(spec.values[0], ast.Constant)):
                return f"$py.format({inner}, {json.dumps(spec.values[0].value)})"
            return f"$py.format({inner}, {self.expr(spec, scope)})"
        return f"$py.str({inner})"

    def x_List(self, node, scope):
        return "[" + ", ".join(self.elt(e, scope) for e in node.elts) + "]"

    x_Tuple = x_List

    def elt(self, e, scope):
        if isinstance(e, ast.Starred):
            return f"...$py.iter({self.expr(e.value, scope)})"
        return self.expr(e, scope)

    def x_Set(self, node, scope):
        return "$py.set([" + ", ".join(self.elt(e, scope) for e in node.elts) + "])"

    def x_Dict(self, node, scope):
        items = []
        for k, v in zip(node.keys, node.values):
            if k is None:
                items.append(f"...{self.expr(v, scope)}")
            elif isinstance(k, ast.Constant) and isinstance(k.value, str):
                items.append(f"{json.dumps(k.value)}: {self.expr(v, scope)}")
            else:
                items.append(f"[{self.expr(k, scope)}]: {self.expr(v, scope)}")
        return "{" + ", ".join(items) + "}"

    def x_Attribute(self, node, scope):
        return f"{self.expr(node.value, scope)}.{node.attr}"

    def x_Subscript(self, node, scope):
        base = self.expr(node.value, scope)
        sl = node.slice
        if isinstance(sl, ast.Slice):
            parts = [self.expr(p, scope) if p is not None else "null" for p in (sl.lower, sl.upper, sl.step)]
            return f"$py.slice({base}, {', '.join(parts)})"
        return f"$py.at({base}, {self.expr(sl, scope)})"

    def x_IfExp(self, node, scope):
        return f"({self.test(node.test, scope)} ? {self.expr(node.body, scope)} : {self.expr(node.orelse, scope)})"

    def x_UnaryOp(self, node, scope):
        if isinstance(node.op, ast.Not):
            return f"!{self.test(node.operand, scope)}"
        op = {ast.USub: "-", ast.UAdd: "+", ast.Invert: "~"}[type(node.op)]
        return f"({op}{self.expr(node.operand, scope)})"

    def x_BinOp(self, node, scope):
        return self.binop(node.op, node.left, node.right, scope)

    def binop(self, op, lnode, rnode, scope, ljs=None):
        a = ljs if ljs is not None else self.expr(lnode, scope)
        b = self.expr(rnode, scope)
        num_l = _is_num(lnode)
        num_r = _is_num(rnode)
        if isinstance(op, ast.Add):
            if (_is_numeric(lnode) and _is_numeric(rnode)) or (_is_str(lnode) and _is_str(rnode)):
                return f"({a} + {b})"
            return f"$py.add({a}, {b})"
        if isinstance(op, ast.Sub):
            return f"({a} - {b})"
        if isinstance(op, ast.Mult):
            if num_l and num_r:
                return f"({a} * {b})"
            return f"$py.mul({a}, {b})"
        if isinstance(op, ast.Div):
            return f"$py.div({a}, {b})" if not num_r else f"({a} / {b})"
        if isinstance(op, ast.FloorDiv):
            return f"$py.floordiv({a}, {b})"
        if isinstance(op, ast.Mod):
            return f"$py.mod({a}, {b})"
        if isinstance(op, ast.Pow):
            return f"({a} ** {b})"
        bit = {ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^", ast.LShift: "<<", ast.RShift: ">>"}
        if type(op) in bit:
            return f"({a} {bit[type(op)]} {b})"
        raise self.error(lnode, f"operator {type(op).__name__} is not supported in browser code")

    def x_BoolOp(self, node, scope):
        if all(self.is_bool(v, scope) for v in node.values):
            js_op = " && " if isinstance(node.op, ast.And) else " || "
            return "(" + js_op.join(self.expr(v, scope) for v in node.values) + ")"
        if self._calls_server(node, scope):
            raise self.error(node, "server calls inside `and`/`or` are not supported; "
                                   "assign the result to a variable first")
        helper = "$py.and" if isinstance(node.op, ast.And) else "$py.or"
        out = self.expr(node.values[-1], scope)
        for v in reversed(node.values[:-1]):
            out = f"{helper}({self.expr(v, scope)}, () => {out})"
        return out

    def x_Compare(self, node, scope):
        parts = []
        left = node.left
        for op, right in zip(node.ops, node.comparators):
            parts.append(self.compare(op, left, right, scope))
            left = right
        return parts[0] if len(parts) == 1 else "(" + " && ".join(parts) + ")"

    def compare(self, op, lnode, rnode, scope):
        a = self.expr(lnode, scope)
        b = self.expr(rnode, scope)
        none_l = isinstance(lnode, ast.Constant) and lnode.value is None
        none_r = isinstance(rnode, ast.Constant) and rnode.value is None
        if isinstance(op, (ast.Is, ast.IsNot)):
            if none_l or none_r:
                return f"({a} {'==' if isinstance(op, ast.Is) else '!='} {b})"
            return f"({a} {'===' if isinstance(op, ast.Is) else '!=='} {b})"
        if isinstance(op, (ast.Eq, ast.NotEq)):
            prim = _is_prim(lnode) or _is_prim(rnode)
            if none_l or none_r:
                return f"({a} {'==' if isinstance(op, ast.Eq) else '!='} {b})"
            if prim:
                return f"({a} {'===' if isinstance(op, ast.Eq) else '!=='} {b})"
            return f"$py.eq({a}, {b})" if isinstance(op, ast.Eq) else f"!$py.eq({a}, {b})"
        if isinstance(op, ast.In):
            return f"$py.contains({b}, {a})"
        if isinstance(op, ast.NotIn):
            return f"!$py.contains({b}, {a})"
        sym = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}[type(op)]
        if isinstance(lnode, (ast.List, ast.Tuple)) or isinstance(rnode, (ast.List, ast.Tuple)):
            return f"($py.cmp({a}, {b}) {sym} 0)"
        return f"({a} {sym} {b})"

    def is_bool(self, node, scope):
        if isinstance(node, ast.Compare):
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            return True
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return True
        if isinstance(node, ast.BoolOp):
            return all(self.is_bool(v, scope) for v in node.values)
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in BOOL_CALLS:
                return True
            if isinstance(f, ast.Attribute) and f.attr in BOOL_METHODS:
                return True
        return False

    def test(self, node, scope):
        """Translate ``node`` for use as a condition (Python truthiness)."""
        if isinstance(node, ast.BoolOp):
            js_op = " && " if isinstance(node.op, ast.And) else " || "
            return "(" + js_op.join(self.test(v, scope) for v in node.values) + ")"
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return f"!{self.test(node.operand, scope)}"
        js = self.expr(node, scope)
        return js if self.is_bool(node, scope) else f"$py.truth({js})"

    def x_Lambda(self, node, scope):
        params, inner = self.params(node.args, scope)
        self.fn_stack.append(_Fn("<lambda>"))
        try:
            body = self.expr(node.body, inner)
        finally:
            fn = self.fn_stack.pop()
        prefix = "async " if fn.is_async else ""
        return f"({prefix}({', '.join(params)}) => ({body}))"

    def params(self, args, scope):
        if args.vararg or args.kwarg or args.kwonlyargs or getattr(args, "posonlyargs", []):
            raise self.error(args, "*args, **kwargs and keyword-only parameters are not supported in browser code")
        names = {}
        out = []
        defaults = [None] * (len(args.args) - len(args.defaults)) + list(args.defaults)
        for a, d in zip(args.args, defaults):
            names[a.arg] = VALUE
            out.append(jsname(a.arg) + (f" = {self.expr(d, scope)}" if d is not None else ""))
        return out, scope.child(names, kind="function")

    def x_Await(self, node, scope):
        self.mark_async()
        return f"(await {self.expr(node.value, scope)})"

    def x_NamedExpr(self, node, scope):
        raise self.error(node, "the walrus operator is not supported in browser code")

    def x_Starred(self, node, scope):
        raise self.error(node, "starred expressions are only supported inside list/tuple literals and calls")

    # comprehensions --------------------------------------------------
    def comp(self, node, scope, elt_fn, wrap):
        for g in node.generators:
            if g.is_async:
                raise self.error(node, "async comprehensions are not supported")
        return wrap(self._gen(node.generators, 0, scope, elt_fn))

    def _gen(self, gens, i, scope, elt_fn):
        g = gens[i]
        it = self.expr(g.iter, scope)
        inner = scope.child({n: VALUE for n in _target_names(g.target)})
        target = self.target_pattern(g.target)
        seq = f"$py.iter({it})"
        for cond in g.ifs:
            seq = f"{seq}.filter(({target}) => {self.test(cond, inner)})"
        if i == len(gens) - 1:
            elt = elt_fn(inner)
            if elt == target and seq != f"$py.iter({it})":
                return seq
            return f"{seq}.map(({target}) => ({elt}))"
        return f"{seq}.flatMap(({target}) => {self._gen(gens, i + 1, inner, elt_fn)})"

    def x_ListComp(self, node, scope):
        return self.comp(node, scope, lambda s: self.expr(node.elt, s), lambda s: s)

    x_GeneratorExp = x_ListComp

    def x_SetComp(self, node, scope):
        return self.comp(node, scope, lambda s: self.expr(node.elt, s), lambda s: f"$py.set({s})")

    def x_DictComp(self, node, scope):
        return self.comp(node, scope,
                         lambda s: f"[{self.expr(node.key, s)}, {self.expr(node.value, s)}]",
                         lambda s: f"Object.fromEntries({s})")

    def target_pattern(self, t):
        if isinstance(t, ast.Name):
            return jsname(t.id)
        if isinstance(t, (ast.Tuple, ast.List)):
            return "[" + ", ".join(self.target_pattern(e) for e in t.elts) + "]"
        raise self.error(t, "only names and tuples can be loop targets in browser code")

    # calls -------------------------------------------------------------
    def args(self, node, scope):
        out = []
        for a in node.args:
            if isinstance(a, ast.Starred):
                out.append(f"...$py.iter({self.expr(a.value, scope)})")
            else:
                out.append(self.expr(a, scope))
        return out

    def kwargs_obj(self, node, scope):
        if not node.keywords:
            return None
        items = []
        for k in node.keywords:
            if k.arg is None:
                items.append(f"...{self.expr(k.value, scope)}")
            else:
                items.append(f"{json.dumps(k.arg)}: {self.expr(k.value, scope)}")
        return "$py.kw({" + ", ".join(items) + "})"

    def x_Call(self, node, scope):
        f = node.func
        if isinstance(f, ast.Name):
            kind = scope.lookup(f.id)
            if kind == SERVER:
                return self.server_call(node, scope)
            if kind == COMPONENT:
                raise self.error(node, f"components are used as tags: <{f.id} ... />, not called")
            if kind is None and f.id == "isinstance" and len(node.args) == 2:
                types = node.args[1].elts if isinstance(node.args[1], ast.Tuple) else [node.args[1]]
                names = []
                for t in types:
                    if not (isinstance(t, ast.Name) and t.id in ("str", "int", "float", "bool", "list",
                                                                "dict", "set", "tuple", "type")):
                        raise self.error(node, "isinstance() in browser code supports builtin types only")
                    names.append(t.id)
                return f"$py.isinstance({self.expr(node.args[0], scope)}, {json.dumps(names)})"
            if kind is None and f.id in BUILTINS:
                args = self.args(node, scope)
                kw = self.kwargs_obj(node, scope)
                if kw:
                    args.append(kw)
                return f"$py.{f.id}({', '.join(args)})"
            if kind == HELPER:
                self.compile_helper(f.id, f)
                call = self.positional_call(node, scope, jsname(f.id), self._params_of(self.ctx.helpers[f.id]))
                if self.ctx.helper_async.get(f.id):
                    self.mark_async()
                    return f"(await {call})"
                return call
            if kind == HANDLER:
                fn_node = scope.lookup("$fn:" + f.id)
                params = self._params_of(fn_node) if isinstance(fn_node, ast.AST) else None
                call = self.positional_call(node, scope, jsname(f.id), params)
                if isinstance(fn_node, ast.AST) and getattr(fn_node, "_pyweb_will_be_async", False):
                    self.mark_async()
                    return f"(await {call})"
                return call
            callee = self.expr(f, scope)
            if node.keywords:
                raise self.error(node, f"keyword arguments are not supported when calling {f.id!r} from browser code")
            return f"{callee}({', '.join(self.args(node, scope))})"
        if isinstance(f, ast.Attribute):
            self.expr(f.value, scope)  # surfaces server-only names before other errors
            root = _root_name(f.value)
            root_kind = scope.lookup(root) if root else None
            js_root = root_kind in ("browser_global",) or (root_kind is None and root in JS_GLOBALS)
            if f.attr in PY_METHODS and not js_root:
                args = self.args(node, scope)
                kw = self.kwargs_obj(node, scope)
                if kw:
                    args.append(kw)
                argstr = "".join(", " + a for a in args)
                path = self.state_path(f.value, scope)
                if path is not None and f.attr in MUTATING:
                    sig, keys = path
                    return f"$py.mut({sig}, [{', '.join(keys)}], ($v) => $py.m($v, {json.dumps(f.attr)}{argstr}))"
                return f"$py.m({self.expr(f.value, scope)}, {json.dumps(f.attr)}{argstr})"
            if node.keywords:
                raise self.error(node, f"keyword arguments are not supported for .{f.attr}() in browser code")
            return f"{self.expr(f, scope)}({', '.join(self.args(node, scope))})"
        if node.keywords:
            raise self.error(node, "keyword arguments are not supported here in browser code")
        return f"{self.expr(f, scope)}({', '.join(self.args(node, scope))})"

    def _params_of(self, fn_node):
        if fn_node is None:
            return None
        a = fn_node.args
        return [p.arg for p in a.args]

    def positional_call(self, node, scope, callee, params):
        args = self.args(node, scope)
        if node.keywords:
            if params is None:
                raise self.error(node, f"keyword arguments are not supported when calling {callee!r}")
            slots = list(args) + ["undefined"] * (len(params) - len(args))
            for k in node.keywords:
                if k.arg is None or k.arg not in params:
                    raise self.error(node, f"{callee}() got an unexpected keyword argument {k.arg!r}")
                slots[params.index(k.arg)] = self.expr(k.value, scope)
            while slots and slots[-1] == "undefined":
                slots.pop()
            args = slots
        return f"{callee}({', '.join(args)})"

    def server_call(self, node, scope):
        name = node.func.id
        params = self.ctx.server_fns[name]
        if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
            raise self.error(node, f"*args/**kwargs are not supported when calling server function {name!r}")
        if len(node.args) > len(params):
            raise self.error(node, f"{name}() takes {len(params)} arguments but {len(node.args)} were given")
        fields = []
        for p, a in zip(params, node.args):
            fields.append(f"{json.dumps(p)}: {self.expr(a, scope)}")
        for k in node.keywords:
            if k.arg not in params:
                raise self.error(node, f"{name}() got an unexpected keyword argument {k.arg!r}")
            fields.append(f"{json.dumps(k.arg)}: {self.expr(k.value, scope)}")
        self.mark_async()
        caller = self.fn_stack[-1].name if self.fn_stack else "<expr>"
        self.ctx.rpc_calls.setdefault(name, set()).add(caller)
        return f"(await $rpc({json.dumps(name)}, {{{', '.join(fields)}}}))"

    def _calls_server(self, node, scope):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                    and scope.lookup(sub.func.id) == SERVER:
                return True
        return False

    def state_path(self, node, scope):
        """``todos[i]["done"]`` rooted at a signal → (signal_js, [key_js...])."""
        keys = []
        cur = node
        while True:
            if isinstance(cur, ast.Name):
                if scope.lookup(cur.id) == SIGNAL:
                    return jsname(cur.id), list(reversed(keys))
                return None
            if isinstance(cur, ast.Subscript):
                if isinstance(cur.slice, ast.Slice):
                    return None
                keys.append(self.expr(cur.slice, scope))
                cur = cur.value
            elif isinstance(cur, ast.Attribute):
                keys.append(json.dumps(cur.attr))
                cur = cur.value
            else:
                return None

    # -------------------------------------------------------- statements
    def block(self, stmts, scope, ind):
        out = []
        for s in stmts:
            out.extend(self.stmt(s, scope, ind))
        return out

    def stmt(self, node, scope, ind):
        m = getattr(self, "s_" + type(node).__name__, None)
        if m is None:
            raise self.error(node, f"{type(node).__name__} statements are not supported in browser code")
        return m(node, scope, ind)

    def s_Expr(self, node, scope, ind):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return []  # docstring
        if _is_ui_placeholder(node):
            return []
        return [f"{ind}{self.expr(node.value, scope)};"]

    def s_Pass(self, node, scope, ind):
        return []

    def s_Break(self, node, scope, ind):
        return [f"{ind}break;"]

    def s_Continue(self, node, scope, ind):
        return [f"{ind}continue;"]

    def s_Global(self, node, scope, ind):
        return []

    s_Nonlocal = s_Global

    def s_Return(self, node, scope, ind):
        if node.value is None:
            return [f"{ind}return;"]
        return [f"{ind}return {self.expr(node.value, scope)};"]

    def s_If(self, node, scope, ind):
        out = [f"{ind}if ({self.test(node.test, scope)}) {{"]
        out += self.block(node.body, scope, ind + "  ")
        orelse = node.orelse
        while len(orelse) == 1 and isinstance(orelse[0], ast.If):
            nxt = orelse[0]
            out.append(f"{ind}}} else if ({self.test(nxt.test, scope)}) {{")
            out += self.block(nxt.body, scope, ind + "  ")
            orelse = nxt.orelse
        if orelse:
            out.append(f"{ind}}} else {{")
            out += self.block(orelse, scope, ind + "  ")
        out.append(f"{ind}}}")
        return out

    def s_While(self, node, scope, ind):
        if node.orelse:
            raise self.error(node, "while/else is not supported in browser code")
        out = [f"{ind}while ({self.test(node.test, scope)}) {{"]
        out += self.block(node.body, scope, ind + "  ")
        out.append(f"{ind}}}")
        return out

    def s_For(self, node, scope, ind):
        if node.orelse:
            raise self.error(node, "for/else is not supported in browser code")
        for n in _target_names(node.target):
            self.ensure_assignable(n, scope, node)
        it = self.expr(node.iter, scope)
        out = [f"{ind}for ({self.target_pattern(node.target)} of $py.iter({it})) {{"]
        out += self.block(node.body, scope, ind + "  ")
        out.append(f"{ind}}}")
        return out

    def ensure_assignable(self, name, scope, node):
        kind = scope.lookup(name)
        if kind in (COMPUTED, CONST, PROP, MODCONST):
            raise self.error(node, f"cannot assign to {name!r}: it is derived/read-only here")
        if kind in (SERVER, HELPER, COMPONENT, SERVER_ONLY):
            raise self.error(node, f"cannot assign to {name!r} in browser code")

    def s_Assign(self, node, scope, ind):
        value = self.expr(node.value, scope)
        if len(node.targets) == 1:
            return self.assign(node.targets[0], value, scope, ind, node)
        t = self.tmp()
        out = [f"{ind}{{ const {t} = {value};"]
        for tgt in node.targets:
            out += self.assign(tgt, t, scope, ind + "  ", node)
        out.append(f"{ind}}}")
        return out

    def s_AnnAssign(self, node, scope, ind):
        if node.value is None:
            return []
        return self.assign(node.target, self.expr(node.value, scope), scope, ind, node)

    def assign(self, target, value, scope, ind, node):
        if isinstance(target, ast.Name):
            self.ensure_assignable(target.id, scope, node)
            kind = scope.lookup(target.id)
            js = jsname(target.id)
            if kind == SIGNAL:
                return [f"{ind}{js}({value});"]
            return [f"{ind}{js} = {value};"]
        if isinstance(target, (ast.Tuple, ast.List)):
            t = self.tmp()
            out = [f"{ind}{{ const {t} = $py.list({value});"]
            if any(isinstance(e, ast.Starred) for e in target.elts):
                raise self.error(node, "starred assignment is not supported in browser code")
            out.append(f"{ind}  if ({t}.length !== {len(target.elts)}) throw $py.error(\"ValueError\", "
                       f"\"expected {len(target.elts)} values to unpack\");")
            for i, e in enumerate(target.elts):
                out += self.assign(e, f"{t}[{i}]", scope, ind + "  ", node)
            out.append(f"{ind}}}")
            return out
        if isinstance(target, (ast.Subscript, ast.Attribute)):
            path = self.state_path(target, scope)
            if path is not None:
                sig, keys = path
                return [f"{ind}$py.setp({sig}, [{', '.join(keys)}], {value});"]
            if isinstance(target, ast.Subscript):
                if isinstance(target.slice, ast.Slice):
                    raise self.error(node, "slice assignment is not supported in browser code")
                return [f"{ind}$py.setitem({self.expr(target.value, scope)}, "
                        f"{self.expr(target.slice, scope)}, {value});"]
            return [f"{ind}{self.expr(target.value, scope)}.{target.attr} = {value};"]
        raise self.error(node, f"cannot assign to {type(target).__name__} in browser code")

    def s_AugAssign(self, node, scope, ind):
        t = node.target
        if isinstance(t, ast.Name):
            self.ensure_assignable(t.id, scope, node)
            js = jsname(t.id)
            kind = scope.lookup(t.id)
            cur = f"{js}()" if kind == SIGNAL else js
            new = self.binop(node.op, t, node.value, scope, ljs=cur)
            if kind == SIGNAL:
                return [f"{ind}{js}({_strip_parens(new)});"]
            return [f"{ind}{js} = {_strip_parens(new)};"]
        path = self.state_path(t, scope)
        if path is not None:
            sig, keys = path
            k = self.tmp("k")
            new = self.binop(node.op, t, node.value, scope, ljs=f"$py.getp({sig}.peek(), {k})")
            return [f"{ind}{{ const {k} = [{', '.join(keys)}]; $py.setp({sig}, {k}, {new}); }}"]
        if isinstance(t, ast.Subscript):
            obj, key = self.tmp("o"), self.tmp("k")
            new = self.binop(node.op, t, node.value, scope, ljs=f"$py.at({obj}, {key})")
            return [f"{ind}{{ const {obj} = {self.expr(t.value, scope)}, {key} = {self.expr(t.slice, scope)}; "
                    f"$py.setitem({obj}, {key}, {new}); }}"]
        if isinstance(t, ast.Attribute):
            obj = self.tmp("o")
            new = self.binop(node.op, t, node.value, scope, ljs=f"{obj}.{t.attr}")
            return [f"{ind}{{ const {obj} = {self.expr(t.value, scope)}; {obj}.{t.attr} = {new}; }}"]
        raise self.error(node, "unsupported augmented assignment target")

    def s_Delete(self, node, scope, ind):
        out = []
        for t in node.targets:
            path = self.state_path(t, scope) if isinstance(t, (ast.Subscript, ast.Attribute)) else None
            if path is not None and path[1]:
                sig, keys = path
                out.append(f"{ind}$py.delp({sig}, [{', '.join(keys)}]);")
            elif isinstance(t, ast.Subscript) and not isinstance(t.slice, ast.Slice):
                out.append(f"{ind}$py.delitem({self.expr(t.value, scope)}, {self.expr(t.slice, scope)});")
            else:
                raise self.error(node, "only `del x[key]` is supported in browser code")
        return out

    def s_Raise(self, node, scope, ind):
        if node.exc is None:
            return [f"{ind}throw $err;"]
        exc = node.exc
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) \
                and scope.lookup(exc.func.id) is None and exc.func.id[:1].isupper():
            msg = self.expr(exc.args[0], scope) if exc.args else "null"
            return [f"{ind}throw $py.error({json.dumps(exc.func.id)}, {msg});"]
        if isinstance(exc, ast.Name) and scope.lookup(exc.id) is None and exc.id[:1].isupper():
            return [f"{ind}throw $py.error({json.dumps(exc.id)}, null);"]
        return [f"{ind}throw {self.expr(exc, scope)};"]

    def s_Assert(self, node, scope, ind):
        msg = self.expr(node.msg, scope) if node.msg is not None else "null"
        return [f"{ind}if (!{self.test(node.test, scope)}) throw $py.error(\"AssertionError\", {msg});"]

    def s_Try(self, node, scope, ind):
        if node.orelse:
            raise self.error(node, "try/else is not supported in browser code")
        out = [f"{ind}try {{"]
        out += self.block(node.body, scope, ind + "  ")
        if node.handlers:
            out.append(f"{ind}}} catch ($err) {{")
            first = True
            for h in node.handlers:
                types = _exc_names(h.type)
                if types is None:
                    raise self.error(h, "except clauses must name exception classes")
                cond = "true" if not types else f"$py.exc($err, {json.dumps(types)})"
                kw = "if" if first else "} else if"
                out.append(f"{ind}  {kw} ({cond}) {{")
                if h.name:
                    self.ensure_assignable(h.name, scope, h)
                    out.append(f"{ind}    {jsname(h.name)} = $err;")
                out += self.block(h.body, scope, ind + "    ")
                first = False
            out.append(f"{ind}  }} else {{ throw $err; }}")
        if node.finalbody:
            out.append(f"{ind}}} finally {{")
            out += self.block(node.finalbody, scope, ind + "  ")
        out.append(f"{ind}}}")
        return out

    def s_FunctionDef(self, node, scope, ind):
        return self.function(node, scope, ind).splitlines()

    s_AsyncFunctionDef = s_FunctionDef

    def s_With(self, node, scope, ind):
        raise self.error(node, "`with` is not supported in browser code")

    def s_Import(self, node, scope, ind):
        raise self.error(node, "imports inside browser code are not supported; import at module level")

    s_ImportFrom = s_Import

    def s_ClassDef(self, node, scope, ind):
        raise self.error(node, "classes are not supported in browser code")

    # --------------------------------------------------------- functions
    def function(self, node, scope, ind="", name=None):
        """Translate a ``def`` to a JS function declaration."""
        if node.decorator_list:
            raise self.error(node, f"decorators are not supported on browser function {node.name!r}")
        params, inner = self.params(node.args, scope)
        local_names = _local_names(node) - {a.arg for a in node.args.args}
        for n in sorted(local_names):
            kind = scope.lookup(n)
            if kind in (SIGNAL,):
                continue  # PyWeb rule: page state is shared with handlers
            if kind in (COMPUTED, CONST, PROP, MODCONST):
                raise self.error(node, f"{node.name}() assigns to {n!r}, which is derived/read-only")
            inner.names[n] = VALUE
        for sub in node.body:
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner.names[sub.name] = HANDLER
                inner.names["$fn:" + sub.name] = sub
        fn = _Fn(node.name)
        self.fn_stack.append(fn)
        try:
            body = self.block(node.body, inner, ind + "  ")
        finally:
            self.fn_stack.pop()
        decl = [n for n in sorted(local_names) if inner.names.get(n) == VALUE
                and not any(isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name == n
                            for s in node.body)]
        is_async = fn.is_async or isinstance(node, ast.AsyncFunctionDef) \
            or getattr(node, "_pyweb_will_be_async", False)
        head = f"{ind}{'async ' if is_async else ''}function {jsname(name or node.name)}({', '.join(params)}) {{"
        lines = [head]
        if decl:
            lines.append(f"{ind}  let {', '.join(jsname(d) for d in decl)};")
        lines += body
        lines.append(f"{ind}}}")
        node._pyweb_async = is_async
        return "\n".join(lines)

    def compile_helper(self, name, at_node):
        if name in self.ctx.helper_js:
            return
        fn = self.ctx.helpers[name]
        self.ctx.helper_js[name] = None  # in progress (recursion guard)
        try:
            saved = self.fn_stack
            self.fn_stack = []
            try:
                js = self.function(fn, self.ctx.module_scope())
            finally:
                self.fn_stack = saved
        except CompileError as exc:
            del self.ctx.helper_js[name]
            raise CompileError(
                f"{name}() is called from browser code but cannot run in the browser: {exc.msg}. "
                f"Mark it @server to run it on the server instead",
                exc.lineno or getattr(at_node, "lineno", None), self.ctx.filename) from None
        self.ctx.helper_js[name] = js
        self.ctx.helper_async[name] = getattr(fn, "_pyweb_async", False)
        self.ctx.used_helpers.append(name)


# ----------------------------------------------------------------- utils

def _is_num(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return True
    return isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and _is_num(node.operand)


_NUMERIC_CALLS = {"len", "int", "float", "abs", "round", "ord"}


def _is_numeric(node):
    """Provably a number (so JS ``+`` matches Python)."""
    if _is_num(node):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _NUMERIC_CALLS:
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Sub, ast.Div, ast.FloorDiv, ast.Pow)):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mult, ast.Mod)):
        return _is_numeric(node.left) and _is_numeric(node.right)
    return isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)) and _is_numeric(node.operand)


def _is_str(node):
    return (isinstance(node, ast.Constant) and isinstance(node.value, str)) or isinstance(node, ast.JoinedStr)


def _is_prim(node):
    return (isinstance(node, ast.Constant) and node.value is not None) or _is_num(node) or isinstance(node, ast.JoinedStr)


def _strip_parens(js):
    return js[1:-1] if js.startswith("(") and js.endswith(")") and _balanced(js[1:-1]) else js


def _balanced(s):
    depth = 0
    for c in s:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _root_name(node):
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def _target_names(t):
    if isinstance(t, ast.Name):
        return [t.id]
    if isinstance(t, (ast.Tuple, ast.List)):
        out = []
        for e in t.elts:
            out += _target_names(e)
        return out
    return []


def _exc_names(t):
    if t is None:
        return []
    if isinstance(t, ast.Name):
        return [t.id]
    if isinstance(t, ast.Attribute):
        return [t.attr]
    if isinstance(t, ast.Tuple):
        out = []
        for e in t.elts:
            sub = _exc_names(e)
            if sub is None:
                return None
            out += sub
        return out
    return None


def _is_ui_placeholder(node):
    v = node.value if isinstance(node, ast.Expr) else None
    return (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
            and v.func.id == "__pyweb_ui__")


def _local_names(fn):
    """Names a function assigns (excluding nested function/lambda/comprehension scopes)."""
    out = set()

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.add(child.name)
                continue
            if isinstance(child, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                out.add(child.id)
            if isinstance(child, ast.ExceptHandler) and child.name:
                out.add(child.name)
            visit(child)

    for stmt in fn.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(stmt.name)
            continue
        if isinstance(stmt, ast.Name):
            continue
        tmp = ast.Module(body=[stmt], type_ignores=[])
        visit(tmp)
    return out


def mark_async_handlers(handlers, scope):
    """Pre-pass: flag handlers that will compile to ``async`` functions.

    A handler is async if it awaits, calls a server function, or calls
    another async handler (fixpoint). Callers then ``await`` it.
    """
    is_async = {}
    for name, fn in handlers.items():
        direct = isinstance(fn, ast.AsyncFunctionDef)
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Await):
                direct = True
            elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                    and scope.lookup(sub.func.id) == SERVER:
                direct = True
        is_async[name] = direct
    changed = True
    while changed:
        changed = False
        for name, fn in handlers.items():
            if is_async[name]:
                continue
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                        and is_async.get(sub.func.id):
                    is_async[name] = changed = True
                    break
    for name, fn in handlers.items():
        fn._pyweb_will_be_async = is_async[name]
    return is_async


def ui_placeholder(node):
    return _is_ui_placeholder(node)
