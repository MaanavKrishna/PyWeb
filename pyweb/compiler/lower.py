"""Lowering: module analysis, state classification, browser JS emission.

This is where PyWeb decides *where code runs*:

* ``@server`` functions run on the server; browser calls become typed RPC.
* Page/component locals are classified per name:

  ========  ==========================================================
  signal    mutated by a handler or bound to an input → reactive state
  computed  derived from reactive state, never assigned → cached derivation
  const     never changes after the page loads
  ========  ==========================================================

  and per *initializer*: a literal, an expression the browser can compute,
  or a **server value** (anything touching server functions, models,
  imports, or page-level Python logic). Server values are computed per
  request and only those the browser actually reads are sent to it.
* Event handlers and ``{expressions}`` are translated to JS
  (:mod:`pyweb.compiler.pyjs`); module helpers are compiled on demand.
  Code that cannot run in a browser is a compile error, not a runtime
  surprise.
"""

from __future__ import annotations

import ast
import json
import os
import re

from . import pyjs
from .ast import ControlFor, ControlIf, Element, ExprNode
from .errors import CompileError
from .pyjs import (COMPUTED, CONST, HANDLER, PROP, SERVER, SIGNAL, VALUE,
                   ModuleContext, Translator, jsname)

RUNTIME_IMPORT = ("import { h as $h, t as $t, dyn as $dyn, list as $list, when as $when, signal as $signal, "
                  "computed as $computed, mount as $mount, onMount as $onMount, py as $py, rpc as $rpc, "
                  "subscribe as $subscribe } "
                  "from \"./runtime.js\";")

SECRET_NAME = re.compile(r"(?i)(secret|password|passwd|api_?key|token|private_?key|credential)")

PAGE_DECORATOR_ATTRS = ("page",)
SERVER_DECORATORS = ("server", "worker", "edge", "task")


# ------------------------------------------------------------ module scan

def _deco_name(d):
    if isinstance(d, ast.Call):
        d = d.func
    if isinstance(d, ast.Name):
        return d.id
    if isinstance(d, ast.Attribute):
        return d.attr
    return ""


def _literal_kwargs(call):
    out = {}
    for k in call.keywords:
        if k.arg is None:
            continue
        try:
            out[k.arg] = ast.literal_eval(k.value)
        except (ValueError, SyntaxError, TypeError):
            pass
    return out


def _page_kwargs(fn):
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and _deco_name(d) in PAGE_DECORATOR_ATTRS:
            return _literal_kwargs(d)
    return {}


def _route_of(fn):
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and _deco_name(d) in PAGE_DECORATOR_ATTRS and d.args:
            a = d.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                return a.value
    return None


def is_ui_stmt(stmt):
    """Statements that exist only because of markup placeholders."""
    if pyjs.ui_placeholder(stmt):
        return True
    if isinstance(stmt, (ast.For, ast.If)):
        body = stmt.body + stmt.orelse
        return bool(body) and all(is_ui_stmt(s) for s in body)
    return False


def has_ui(fn):
    return any(is_ui_stmt(s) for s in fn.body)


class PageInfo:
    def __init__(self, node, kind, route=None):
        self.node = node
        self.name = node.name
        self.kind = kind            # "page" | "component"
        self.route = route
        self.title = _page_kwargs(node).get("title") if kind == "page" else None
        self.ui = []
        self.params = [a.arg for a in node.args.args]
        self.defaults = {}
        args = node.args
        for a, d in zip(args.args[len(args.args) - len(args.defaults):], args.defaults):
            self.defaults[a.arg] = d
        self.handlers = {}          # name -> FunctionDef
        self.inits = {}             # name -> value node (single simple assignment)
        self.order = []             # state names in assignment order
        self.server_logic = []      # non-assignment statements (run on the server)
        self.server_assigned = set()
        self.kinds = {}             # name -> SIGNAL/COMPUTED/CONST
        self.origin = {}            # name -> "literal" | "browser" | "server"
        self.reasons = {}           # name -> (location, reason) for `inspect`
        self.literal = {}           # name -> python literal value
        self.js_init = {}           # name -> js expression for browser-computable init
        self.deps = {}              # name -> names read by initializer
        self.mutated = {}           # name -> reason
        self.sent = []              # names sent in the page state payload
        self.needs_js = False
        self.js_body = ""


def npm_alias(spec, export):
    """The JavaScript name a page module imports ``npm(spec, export)`` as."""
    import hashlib
    tag = hashlib.sha1(f"{spec}|{export}".encode()).hexdigest()[:6]
    return f"$npm_{re.sub(r'[^A-Za-z0-9]', '_', spec)}_{re.sub(r'[^A-Za-z0-9]', '_', export)}_{tag}"


def _npm_binding(node, ctx, filename):
    """``Name = npm("pkg", "Export")`` at module level -> a browser binding."""
    if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call) and _deco_name(node.value) == "npm"):
        return False
    args = node.value.args
    if not args or len(args) > 2 or node.value.keywords \
            or not all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in args):
        raise CompileError('npm() takes literal strings: npm("package") or npm("package", "Export")',
                           node.lineno, filename)
    spec = args[0].value
    export = args[1].value if len(args) == 2 else "default"
    name = node.targets[0].id
    alias = npm_alias(spec, export)
    ctx.npm_bindings[name] = (spec, export, node.lineno)
    ctx.npm_aliases[alias] = (spec, export)
    ctx.browser_globals[name] = alias
    return True


def _import_library(ctx, lib, node, filename):
    """Bind the names of ``from <lib> import ...`` (another .pyweb file)."""
    where = os.path.basename(lib.path)
    if lib not in ctx.libraries:
        ctx.libraries.append(lib)
        ctx.npm_aliases.update(lib.ctx.npm_aliases)
    for alias in node.names:
        name, local = alias.name, alias.asname or alias.name
        if name == "*":
            raise CompileError(f"import names from {where} explicitly (`from {node.module} import Card, ...`)",
                               node.lineno, filename)
        if local in ctx.components or local in ctx.server_fns:
            raise CompileError(f"{local!r} is imported twice", node.lineno, filename)
        if name in lib.components:
            ctx.components[local] = lib.components[name]
            ctx.imported_components[local] = (lib, name)
        elif name in lib.ctx.server_fns:
            if local != name:
                raise CompileError(f"import server function {name!r} from {where} without `as` "
                                   "(its RPC name is its own name)", node.lineno, filename)
            ctx.server_fns[name] = lib.ctx.server_fns[name]
        elif name in lib.ctx.modconsts:
            ctx.modconsts[local] = lib.ctx.modconsts[name]
        elif name in lib.ctx.npm_bindings:
            spec, export, _line = lib.ctx.npm_bindings[name]
            ctx.npm_bindings[local] = (spec, export, node.lineno)
            ctx.browser_globals[local] = npm_alias(spec, export)
        elif name in lib.defined:
            ctx.server_only[local] = (f"imported from {where}; browser code can use components, "
                                      "@server functions and constants from other .pyweb files")
        else:
            raise CompileError(f"{where} has no {name!r}", node.lineno, filename)


def scan_module(tree, ui_all, filename, resolve=None):
    """Collect module facts and the pages/components it defines.

    ``resolve(module_name, lineno)`` returns a compiled library for
    ``from <module_name> import ...`` when that names another .pyweb file.
    """
    import importlib
    browser_api = importlib.import_module("pyweb.browser")
    ctx = ModuleContext(filename)
    pages, components = [], []
    browser_bindings = browser_api.bindings()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", None) or ""
            lib = None
            if isinstance(node, ast.ImportFrom) and mod and not node.level and resolve:
                lib = resolve(mod, node.lineno)
            if lib is not None:
                _import_library(ctx, lib, node, filename)
                continue
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                if mod == "pyweb" and alias.name == "subscribe":
                    ctx.browser_globals[local] = "$subscribe"
                elif mod == "pyweb.browser" and alias.name in browser_bindings:
                    ctx.browser_globals[local] = browser_bindings[alias.name]
                elif mod == "pyweb.browser" and alias.name in pyjs.JS_GLOBALS:
                    ctx.browser_globals[local] = alias.name
                else:
                    where = f"from {mod} import" if isinstance(node, ast.ImportFrom) else "import"
                    ctx.server_only[local] = f"imported with `{where} {alias.name}`"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decos = [_deco_name(d) for d in node.decorator_list]
            route = _route_of(node)
            if route is not None or any(d in PAGE_DECORATOR_ATTRS for d in decos):
                pages.append(PageInfo(node, "page", route or "/"))
            elif any(d in SERVER_DECORATORS for d in decos):
                a = node.args
                if a.vararg or a.kwarg:
                    raise CompileError(f"@server function {node.name!r} cannot take *args/**kwargs "
                                       "(RPC arguments are named)", node.lineno, filename)
                ctx.server_fns[node.name] = [p.arg for p in a.args + a.kwonlyargs]
            elif "component" in decos or (not decos and has_ui(node)):
                if not node.name[:1].isupper():
                    raise CompileError(f"component {node.name!r} must start with a capital letter "
                                       "(lowercase tags are HTML elements)", node.lineno, filename)
                if node.name in ctx.imported_components:
                    raise CompileError(f"component {node.name!r} is both imported and defined here",
                                       node.lineno, filename)
                components.append(PageInfo(node, "component"))
                ctx.components[node.name] = components[-1]
            elif decos:
                ctx.server_only[node.name] = f"decorated with @{decos[0]}"
            else:
                ctx.helpers[node.name] = node
        elif isinstance(node, ast.ClassDef):
            ctx.server_only[node.name] = "a class (classes and models live on the server)"
        elif _npm_binding(node, ctx, filename):
            continue
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            if isinstance(node.value, ast.Call) and _deco_name(node.value) == "App":
                ctx.app_config = _literal_kwargs(node.value)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            for t in targets:
                for name in _names(t):
                    try:
                        if value is None:
                            raise ValueError
                        ctx.modconsts[name] = ast.literal_eval(value)
                    except (ValueError, SyntaxError, TypeError):
                        ctx.server_only[name] = "a module-level object created on the server"
                        ctx.modconsts.pop(name, None)
    if not pages:
        # Single-page fallback: the first function with markup is "/".
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and has_ui(node) \
                    and node.name not in ctx.components:
                pages.append(PageInfo(node, "page", "/"))
                break
    for info in pages + components:
        span = (info.node.lineno, getattr(info.node, "end_lineno", info.node.lineno))
        info.ui = [n for n in ui_all if span[0] <= getattr(n, "line", 0) <= span[1]]
    return ctx, pages, components


def _names(t):
    if isinstance(t, ast.Name):
        return [t.id]
    if isinstance(t, (ast.Tuple, ast.List)):
        out = []
        for e in t.elts:
            out += _names(e)
        return out
    return []


# ---------------------------------------------------------- UI analysis

def _expr_names(code):
    try:
        tree = ast.parse(code, mode="eval")
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def ui_refs(nodes):
    """(names read by markup, bind targets with their lines)."""
    names, binds = set(), {}

    def walk(ns):
        for n in ns:
            if isinstance(n, ExprNode):
                names.update(_expr_names(n.code))
            elif isinstance(n, Element):
                for key, val in n.attrs.items():
                    if not isinstance(val, tuple) or val[0] != "expr":
                        continue
                    if key in ("bind", "ref"):
                        binds[val[1]] = (val[2], key)
                    names.update(_expr_names(val[1]))
                walk(n.children)
            elif isinstance(n, ControlFor):
                names.update(_expr_names(n.iterable))
                walk(n.body)
            elif isinstance(n, ControlIf):
                names.update(_expr_names(n.test))
                walk(n.body)
                walk(n.orelse)

    walk(nodes)
    return names, binds


def used_components(nodes):
    out = []

    def walk(ns):
        for n in ns:
            if isinstance(n, Element):
                if n.is_component and n.tag not in out:
                    out.append(n.tag)
                walk(n.children)
            elif isinstance(n, (ControlFor, ControlIf)):
                walk(n.body)
                walk(getattr(n, "orelse", []))

    walk(nodes)
    return out


def _has_events(nodes):
    for n in nodes:
        if isinstance(n, Element):
            if any(k.startswith("on") or k in ("bind", "ref") for k in n.attrs) or _has_events(n.children):
                return True
            if n.is_component:
                return True
        elif isinstance(n, (ControlFor, ControlIf)):
            if _has_events(n.body) or _has_events(getattr(n, "orelse", [])):
                return True
    return False


def _mutations(fn, state):
    """Map state name -> description of how handler ``fn`` mutates it."""
    out = {}
    local = pyjs._local_names(fn) - {a.arg for a in fn.args.args}
    for name in local & state:
        out[name] = f"assigned in {fn.name}()"
    for sub in ast.walk(fn):
        root = None
        how = ""
        if isinstance(sub, ast.AugAssign):
            root, how = pyjs._root_name(sub.target), "updated"
        elif isinstance(sub, (ast.Assign, ast.Delete)):
            for t in getattr(sub, "targets", []):
                if isinstance(t, (ast.Subscript, ast.Attribute)):
                    root, how = pyjs._root_name(t), "item assigned"
        elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                and sub.func.attr in pyjs.MUTATING:
            root, how = pyjs._root_name(sub.func.value), f".{sub.func.attr}()"
        if root in state and root not in out:
            out[root] = f"{how} in {fn.name}()"
    return out


# ------------------------------------------------------- classification

def classify(info, ctx):
    """Decide signal/computed/const and browser/server origin for each local."""
    fn = info.node
    state_names = set()
    seen = {}
    for stmt in fn.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if stmt.decorator_list:
                raise CompileError(f"decorators are not supported on handler {stmt.name!r}; "
                                   "define @server functions at module level", stmt.lineno, ctx.filename)
            info.handlers[stmt.name] = stmt
            continue
        if is_ui_stmt(stmt):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
                and isinstance(stmt.value.value, str):
            continue
        target = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            target, value = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            target, value = stmt.target.id, stmt.value
        if target is not None:
            if target in seen:
                info.server_assigned.add(target)
            seen[target] = stmt
            info.inits[target] = value
            if target not in info.order:
                info.order.append(target)
            state_names.add(target)
            continue
        info.server_logic.append(stmt)
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                info.server_assigned.add(sub.id)
                state_names.add(sub.id)
                if sub.id not in info.order:
                    info.order.append(sub.id)
    if info.kind == "component" and info.server_logic:
        raise CompileError(f"component {info.name!r} can only contain state assignments, handlers "
                           "and markup; move server logic into the page or an @server function",
                           info.server_logic[0].lineno, ctx.filename)

    ui_names, binds = ui_refs(info.ui)
    for name, (line, key) in binds.items():
        if name not in state_names:
            raise CompileError(f"{key}={{{name}}} must name a local variable of {info.name}()",
                               line, ctx.filename)
        why = "bound to an input" if key == "bind" else "set to an element by ref="
        info.mutated.setdefault(name, f"{why} (line {line})")
    for h in info.handlers.values():
        for name, why in _mutations(h, state_names).items():
            info.mutated.setdefault(name, why)

    params = set(info.params)
    tr = Translator(ctx)
    scope = ctx.module_scope().child(kind="page")
    for p in info.params:
        scope.names[p] = PROP if info.kind == "component" else CONST
    for h in info.handlers:
        scope.names[h] = HANDLER
        scope.names["$fn:" + h] = info.handlers[h]
    for name in info.order:
        value = info.inits.get(name)
        mutated = name in info.mutated
        if name in params:
            raise CompileError(f"{name!r} is a parameter of {info.name}() and cannot be reassigned",
                               getattr(value, "lineno", None), ctx.filename)
        origin, js = "server", None
        if name not in info.server_assigned and value is not None:
            info.deps[name] = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
            try:
                info.literal[name] = ast.literal_eval(value)
                origin = "literal"
            except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
                pass
            try:
                tr.fn_stack = [pyjs._Fn("<init>")]
                js = tr.expr(value, scope)
                if tr.fn_stack[-1].is_async:
                    js = None
                elif origin != "literal":
                    origin = "browser"
            except CompileError:
                js = None
            finally:
                tr.fn_stack = []
        calls_helper = value is not None and any(
            isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in ctx.helpers
            for c in ast.walk(value))
        if origin == "browser" and calls_helper and not tr.is_reactive(value, scope):
            # Loading data (`rows = load_rows()`) runs on the server even when
            # the helper happens to be browser-compatible: only the result ships.
            origin, js = "server", None
        if origin == "browser" and any(info.origin.get(d) == "server" for d in info.deps.get(name, ())):
            # Derived from server data: compute it on the server and send only
            # the result, so the inputs (e.g. a whole session/user record) stay private.
            origin, js = "server", None
        info.origin[name] = origin
        info.js_init[name] = js
        reactive_deps = js is not None and tr.is_reactive(value, scope)
        if mutated:
            kind = SIGNAL
        elif origin == "browser" and reactive_deps:
            kind = COMPUTED
        else:
            kind = CONST
        if info.kind == "component" and origin == "server":
            raise CompileError(f"component state {name!r} must be computable in the browser; "
                               "pass server data in as a prop", getattr(value, "lineno", fn.lineno),
                               ctx.filename)
        info.kinds[name] = kind
        scope.names[name] = kind
    pyjs.mark_async_handlers(info.handlers, scope)
    info.scope = scope
    info.ui_names = ui_names

    # What the browser reads: markup + handlers + derived initializers.
    refs = set(ui_names)
    for h in info.handlers.values():
        refs |= {n.id for n in ast.walk(h) if isinstance(n, ast.Name)}
    changed = True
    while changed:
        changed = False
        for name in list(refs):
            if info.kinds.get(name) == COMPUTED or (info.kinds.get(name) == CONST and info.origin.get(name) == "browser"):
                extra = info.deps.get(name, set()) - refs
                if extra:
                    refs |= extra
                    changed = True
    info.refs = refs
    info.needs_js = bool(info.handlers) or any(k == SIGNAL for k in info.kinds.values()) \
        or _has_events(info.ui)

    sent = []
    if info.kind == "page" and info.needs_js:
        for p in info.params:
            if p in refs:
                sent.append(p)
        for name in info.order:
            if name not in refs:
                continue
            k, o = info.kinds[name], info.origin[name]
            if k == SIGNAL or (k == CONST and o == "server"):
                sent.append(name)
    # Secrets: anything browser code or markup reads ends up in the browser
    # (HTML or JSON state), so secret-looking values are a compile error.
    # Empty form state (`password = ""` bound to an input) is user input.
    for name in sorted(refs & set(info.order)):
        if not SECRET_NAME.search(name):
            continue
        lit = info.literal.get(name, None)
        if info.origin.get(name) == "server" or lit not in (None, "", 0, False):
            raise CompileError(
                f"server secret {name!r} would be sent to the browser (markup or browser code "
                "reads it). Keep secrets inside @server functions",
                getattr(info.inits.get(name), "lineno", fn.lineno), ctx.filename)
    info.sent = sent

    # Placement report for `pyweb inspect`.
    for name in info.order:
        k, o = info.kinds[name], info.origin[name]
        if name not in refs and o == "server":
            info.reasons[name] = ("server", "computed per request; never read by browser code, never sent")
        elif k == SIGNAL:
            src = {"literal": "literal initial value", "browser": "initial value computed in the browser",
                   "server": "initial value computed on the server and sent"}[o]
            info.reasons[name] = ("browser", f"reactive state: {info.mutated[name]}; {src}")
        elif k == COMPUTED:
            deps = sorted(d for d in info.deps.get(name, ()) if info.kinds.get(d) in (SIGNAL, COMPUTED))
            info.reasons[name] = ("browser", f"derived from {', '.join(deps)}; recomputed when they change")
        elif o == "server":
            info.reasons[name] = ("server", "computed per request on the server; value sent because browser code reads it")
        else:
            info.reasons[name] = ("browser", "constant")
    for h in info.handlers:
        info.reasons[h] = ("browser", "event handler (compiled to JavaScript)")
    return info


# --------------------------------------------------------------- codegen

class Emitter:
    def __init__(self, ctx):
        self.ctx = ctx
        self.tr = Translator(ctx)
        self.component_js = {}

    def page_js(self, info):
        """The browser module for one page (``""`` if the page is static)."""
        if not info.needs_js:
            info.js_body = ""
            return ""
        fn_js = self.function_js(info)
        parts = self.module_parts(used_components(info.ui))
        parts.append(fn_js)
        parts.append(f"$mount({json.dumps(info.name)}, {jsname(info.name)});")
        body = "\n".join(parts) + "\n"
        imports = [RUNTIME_IMPORT]
        info.npm = []
        for alias in sorted(set(re.findall(r"\$npm_[A-Za-z0-9_]+", body))):
            spec, export = self.ctx.npm_aliases[alias]
            info.npm.append(spec)
            if export == "default":
                imports.append(f"import {alias} from {json.dumps(spec)};")
            elif export == "*":
                imports.append(f"import * as {alias} from {json.dumps(spec)};")
            else:
                imports.append(f"import {{ {export} as {alias} }} from {json.dumps(spec)};")
        info.npm = sorted(set(info.npm))
        info.js_body = fn_js
        return "\n".join(imports) + "\n" + body

    def module_parts(self, names):
        """JS for components ``names`` (and what they use): blocks for other
        .pyweb files, then this file's constants, helpers and components."""
        local, imports = self.components_for(names)
        parts = [lib.emitter.library_block(wanted) for lib, wanted in imports.values()]
        for name in sorted(self.ctx.used_consts):
            parts.append(f"const {jsname(name)} = {json.dumps(self.ctx.modconsts[name])};")
        for name in self.ctx.used_helpers:
            parts.append(self.ctx.helper_js[name])
        parts += [self.component_js[name] for name in local]
        return parts

    def library_block(self, wanted):
        """Components from this (imported) file, in their own scope so their
        constants and helpers can't clash with the importer's names."""
        inner = self.module_parts([orig for orig, _ in wanted])
        body = "\n".join("  " + line for part in inner for line in part.splitlines())
        names = ", ".join(jsname(orig) for orig, _ in wanted)
        binds = ", ".join(jsname(orig) if orig == local else f"{jsname(orig)}: {jsname(local)}"
                          for orig, local in wanted)
        where = os.path.basename(self.ctx.filename)
        return f"// {where}\nconst {{ {binds} }} = (() => {{\n{body}\n  return {{ {names} }};\n}})();"

    def components_for(self, names, out=None, imports=None):
        out = [] if out is None else out
        imports = {} if imports is None else imports
        for name in names:
            comp = self.ctx.components.get(name)
            if comp is None:
                continue
            source = self.ctx.imported_components.get(name)
            if source is not None:
                lib, orig = source
                wanted = imports.setdefault(lib.path, (lib, []))[1]
                if (orig, name) not in wanted:
                    wanted.append((orig, name))
                continue
            if name not in self.component_js:
                self.component_js[name] = ""  # recursion guard
                self.component_js[name] = self.function_js(comp)
            self.components_for(used_components(comp.ui), out, imports)
            if name not in out:
                out.append(name)
        return out, imports

    def function_js(self, info):
        scope = info.scope
        lines = []
        arg = "$p" if info.kind == "component" else "$s"
        lines.append(f"function {jsname(info.name)}({arg}) {{")
        for p in info.params:
            js = jsname(p)
            if info.kind == "component":
                d = info.defaults.get(p)
                default = self.tr.expr(d, scope.parent) if d is not None else "null"
                lines.append(f"  const {js} = {arg}[{json.dumps(p)}] || ({_thunk(default)});")
            elif p in info.refs:
                lines.append(f"  const {js} = {arg}[{json.dumps(p)}];")
        for name in info.order:
            if name not in info.refs and info.kinds[name] != SIGNAL:
                continue
            js = jsname(name)
            kind, origin, init = info.kinds[name], info.origin[name], info.js_init[name]
            key = json.dumps(name)
            if kind == COMPUTED:
                lines.append(f"  const {js} = $computed({_thunk(init)});")
                continue
            if origin == "server" or init is None:
                fallback = "null"
            else:
                fallback = init
            if kind == SIGNAL:
                if info.kind == "page":
                    lines.append(f"  const {js} = $signal({key} in $s ? $s[{key}] : {fallback});")
                else:
                    lines.append(f"  const {js} = $signal({fallback});")
            elif origin == "server":
                lines.append(f"  const {js} = $s[{key}];")
            else:
                lines.append(f"  const {js} = {fallback};")
        for hname, hnode in info.handlers.items():
            lines.append(self.tr.function(hnode, scope, "  "))
        if "on_mount" in info.handlers:
            mh = info.handlers["on_mount"]
            if mh.args.args:
                raise CompileError("on_mount() takes no parameters", mh.lineno, self.ctx.filename)
            lines.append("  $onMount(on_mount);")
        lines.append(f"  return {self.ui_js(info.ui, scope, '  ')};")
        lines.append("}")
        return "\n".join(lines)

    # markup → JS -------------------------------------------------------
    def ui_js(self, nodes, scope, ind):
        items = [self.node_js(n, scope, ind + "  ") for n in nodes]
        items = [i for i in items if i]
        if not items:
            return "[]"
        if len(items) == 1:
            return f"[{items[0]}]"
        return "[\n" + ",\n".join(ind + "  " + i for i in items) + "\n" + ind + "]"

    def _parse(self, code, line):
        try:
            return ast.parse(code, mode="eval").body
        except SyntaxError as exc:
            raise CompileError(f"invalid expression {{{code}}}: {exc.msg}", line, self.ctx.filename) from None

    def _expr(self, code, line, scope):
        node = self._parse(code, line)
        _relocate(node, line)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id in self.ctx.npm_bindings and not _inside_lambda(node, sub):
                spec = self.ctx.npm_bindings[sub.id][0]
                raise CompileError(f"{{{code}}} uses {sub.id!r} from npm(\"{spec}\"), which only exists in the "
                                   "browser, but markup is first rendered on the server. Use it in an event "
                                   "handler or on_mount (with ref= for elements)", line, self.ctx.filename)
        if self.tr._calls_server(node, scope):
            raise CompileError(f"{{{code}}} calls a server function; markup expressions must be "
                               "synchronous. Load the value in a page variable or a handler",
                               line, self.ctx.filename)
        self.tr.fn_stack = [pyjs._Fn("<markup>")]
        try:
            js = self.tr.expr(node, scope)
        finally:
            self.tr.fn_stack = []
        return node, js

    def node_js(self, n, scope, ind):
        t = type(n).__name__
        # Every child is a call, so children are created (or, when hydrating,
        # claimed from the server HTML) in document order.
        if t == "TextNode":
            return f"$t({json.dumps(n.text)})"
        if t == "ExprNode":
            node, js = self._expr(n.code, n.line, scope)
            if self.tr.is_reactive(node, scope):
                return f"$dyn({_thunk(js)})"
            return f"$t($py.text({js}))"
        if t == "ControlFor":
            it_node, it_js = self._expr(n.iterable, n.line, scope)
            target = self._parse(n.target, n.line)
            inner = scope.child({name: VALUE for name in pyjs._target_names(target)})
            try:
                pattern = self.tr.target_pattern(target)
            except CompileError as exc:
                raise exc.with_file(self.ctx.filename)
            body = self.ui_js(n.body, inner, ind)
            return f"$list({_thunk(it_js)}, ({pattern}) => {body})"
        if t == "ControlIf":
            test_node = self._parse(n.test, n.line)
            _relocate(test_node, n.line)
            self.tr.fn_stack = [pyjs._Fn("<markup>")]
            try:
                test = self.tr.test(test_node, scope)
            finally:
                self.tr.fn_stack = []
            yes = self.ui_js(n.body, scope, ind)
            no = self.ui_js(n.orelse, scope, ind) if n.orelse else None
            return f"$when(() => {test}, () => {yes}" + (f", () => {no})" if no else ")")
        if t == "Element":
            if n.is_component:
                return self.component_call(n, scope, ind)
            return self.element_js(n, scope, ind)
        return ""

    def element_js(self, n, scope, ind):
        props = []
        for key, val in n.attrs.items():
            name = "class" if key in ("class_", "className") else key
            if val is True:
                props.append(f"{json.dumps(name)}: true")
                continue
            kind, body, line = val
            if kind == "lit":
                props.append(f"{json.dumps(name)}: {json.dumps(body)}")
                continue
            if key in ("bind", "ref"):
                if scope.lookup(body) != SIGNAL:
                    raise CompileError(f"{key}={{{body}}} must name a page variable", line, self.ctx.filename)
                props.append(f"\"${key}\": {jsname(body)}")
                continue
            if key.startswith("on"):
                props.append(f"{json.dumps(name)}: {self.handler_js(body, line, scope)}")
                continue
            node, js = self._expr(body, line, scope)
            if self.tr.is_reactive(node, scope):
                props.append(f"{json.dumps(name)}: {_thunk(js)}")
            else:
                props.append(f"{json.dumps(name)}: {js}")
        children = self.ui_js(n.children, scope, ind) if n.children else None
        p = "{" + ", ".join(props) + "}" if props else "null"
        if children:
            # A thunk, so children are created (or claimed, when hydrating)
            # after their parent, in document order.
            return f"$h({json.dumps(n.tag)}, {p}, () => {children})"
        return f"$h({json.dumps(n.tag)}, {p})"

    def handler_js(self, code, line, scope):
        node = self._parse(code, line)
        _relocate(node, line)
        if isinstance(node, ast.Name):
            kind = scope.lookup(node.id)
            if kind == HANDLER:
                return jsname(node.id)
            if kind == PROP:
                return f"(e) => {jsname(node.id)}()(e)"
            if kind == SERVER:
                raise CompileError(f"event handler {node.id!r} is a server function; call it from a "
                                   "handler or use a lambda: onclick={lambda: " + node.id + "(...)}",
                                   line, self.ctx.filename)
        if isinstance(node, ast.Lambda):
            self.tr.fn_stack = []
            return self.tr.expr(node, scope)
        # Any other expression runs when the event fires: onclick={remove(todo)}
        fn = pyjs._Fn("<event>")
        self.tr.fn_stack = [fn]
        try:
            js = self.tr.expr(node, scope)
        finally:
            self.tr.fn_stack = []
        return f"({'async ' if fn.is_async else ''}() => {js})"

    def component_call(self, n, scope, ind):
        if n.tag not in self.ctx.components:
            raise CompileError(f"unknown component <{n.tag}>: define `def {n.tag}(...)` with markup "
                               f"in this file, or import it (`from widgets import {n.tag}`)",
                               n.line, self.ctx.filename)
        comp = self.ctx.components[n.tag]
        props = []
        for key, val in n.attrs.items():
            if key not in comp.params:
                raise CompileError(f"<{n.tag}> has no prop {key!r} (props: {', '.join(comp.params) or 'none'})",
                                   n.line, self.ctx.filename)
            if val is True:
                props.append(f"{json.dumps(key)}: () => true")
            elif val[0] == "lit":
                props.append(f"{json.dumps(key)}: () => {json.dumps(val[1])}")
            else:
                if key.startswith("on"):
                    props.append(f"{json.dumps(key)}: {_thunk(self.handler_js(val[1], val[2], scope))}")
                else:
                    _node, js = self._expr(val[1], val[2], scope)
                    props.append(f"{json.dumps(key)}: {_thunk(js)}")
        if n.children:
            if "children" not in comp.params:
                raise CompileError(f"<{n.tag}> does not accept children (add a `children` parameter)",
                                   n.line, self.ctx.filename)
            props.append(f"\"children\": () => {self.ui_js(n.children, scope, ind)}")
        missing = [p for p in comp.params if p not in comp.defaults and p not in n.attrs
                   and not (p == "children" and n.children)]
        if missing:
            raise CompileError(f"<{n.tag}> is missing required prop(s): {', '.join(missing)}",
                               n.line, self.ctx.filename)
        return f"{jsname(n.tag)}({{{', '.join(props)}}})"


def _inside_lambda(root, target):
    for sub in ast.walk(root):
        if isinstance(sub, ast.Lambda) and any(n is target for n in ast.walk(sub)):
            return True
    return False


def _thunk(js):
    """``() => js``, parenthesised when ``js`` would parse as a block."""
    return f"() => ({js})" if js.lstrip().startswith("{") else f"() => {js}"


def _relocate(node, line):
    """Give parsed markup expressions their real `.pyweb` line number."""
    for sub in ast.walk(node):
        if hasattr(sub, "lineno"):
            sub.lineno = line
            sub.end_lineno = line
