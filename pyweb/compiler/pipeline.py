"""Top-level pipeline: `.pyweb` source → app graph + per-page HTML/JS.

``compile_source`` is pure: it never imports or executes the app. Page
HTML produced here is a *static prerender* (literal and browser-computable
values only). ``pyweb dev``/``serve``/ASGI render pages per request with
real server values via :mod:`pyweb.app_loader`.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import os

from . import parser as P
from .codegen.ir import build_graph, to_text
from .errors import CompileError
from .lower import Emitter, classify, scan_module
from .rpc import rpc_specs


def static_state(info, ctx, args=None):
    """Evaluate a page/component's state at compile time.

    Literals and expressions over them are evaluated with Python; anything
    needing the server (or failing) is ``None``.
    """
    env = {"__builtins__": builtins}
    env.update(static_globals(ctx))
    env.update(args or {})
    for name in info.order:
        value = info.inits.get(name)
        if value is None or info.origin.get(name) == "server":
            env.setdefault(name, None)
            continue
        try:
            env[name] = eval(compile(ast.Expression(value), "<pyweb-static>", "eval"), env)  # noqa: S307
        except Exception:  # noqa: BLE001
            env[name] = None
    env.pop("__builtins__", None)
    return env


def static_globals(ctx):
    """Module constants plus browser-safe helpers (pure Python) for prerendering."""
    cached = getattr(ctx, "_static_globals", None)
    if cached is not None:
        return cached
    env = {"__builtins__": builtins}
    env.update(ctx.modconsts)
    safe = [ctx.helpers[n] for n, js in ctx.helper_js.items() if js]
    if safe:
        mod = ast.Module(body=safe, type_ignores=[])
        try:
            exec(compile(mod, ctx.filename, "exec"), env)  # noqa: S102 - pure helpers
        except Exception:  # noqa: BLE001
            pass
    env.pop("__builtins__", None)
    ctx._static_globals = env
    return env


class Library:
    """Another .pyweb file imported with ``from <name> import ...``."""

    def __init__(self, name, path, compiled, tree):
        self.name, self.path = name, path
        self.ctx = compiled["context"]
        self.components = compiled["components"]
        self.rpc = compiled["rpc"]
        self.libraries = compiled["libraries"]
        self.emitter = compiled["emitter"]
        self.defined = {n for node in tree.body for n in _defined_names(node)}


def _defined_names(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return [a.asname or a.name.split(".")[0] for a in node.names]
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return [n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]
    return []


def _resolver(filename, libs, stack):
    """Find ``<module>.pyweb`` next to ``filename`` and compile it (once)."""
    if not filename or filename.startswith("<") or not os.path.isfile(filename):
        return None
    base = os.path.dirname(os.path.abspath(filename))

    def resolve(module, lineno):
        path = os.path.join(base, *module.split(".")) + ".pyweb"
        if not os.path.isfile(path):
            return None
        path = os.path.abspath(path)
        if path in stack:
            chain = " -> ".join(os.path.basename(p) for p in (*stack, path))
            raise CompileError(f"circular import between .pyweb files: {chain}", lineno, filename)
        if path not in libs:
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            compiled = compile_source(source, filename=path, _libs=libs, _stack=(*stack, path))
            if compiled["pages"]:
                first = next(iter(compiled["pages"].values()))
                raise CompileError(f"pages belong in the app file; {os.path.basename(path)} can only "
                                   "define components, @server functions, helpers and constants",
                                   first["lineno"], path)
            libs[path] = Library(module, path, compiled, P.parse_source(source, path)[0])
        return libs[path]

    return resolve


def _renderer(ctx, globals_=None, strict=False):
    from pyweb.ssr import NS_KEY, Renderer

    def component_state(name, props, ns=None):
        table = ns or ctx
        comp = table.components[name]
        cctx = getattr(comp, "ctx", table)
        args = {}
        for p in comp.params:
            if p in props:
                args[p] = props[p]
            elif p in comp.defaults:
                try:
                    args[p] = ast.literal_eval(comp.defaults[p])
                except ValueError:
                    args[p] = None
            else:
                args[p] = None
        env = static_state(comp, cctx, args)
        env[NS_KEY] = cctx
        return comp.ui, env

    return Renderer(globals_ or static_globals(ctx), component_state=component_state, strict=strict)


def compile_source(source, filename="<pyweb>", route="/", title="PyWeb", *, _libs=None, _stack=None):
    try:
        if _stack is None and filename and not filename.startswith("<"):
            _stack = (os.path.abspath(filename),)
        return _compile(source, filename, route, title, {} if _libs is None else _libs, _stack or ())
    except CompileError as exc:
        if not exc.filename or exc.filename == "<pyweb>":
            exc.with_file(filename)
        raise


def _compile(source, filename, route, title, libs, stack):
    from pyweb.ssr import page_html
    tree, ui_all, _pages = P.parse_source(source, filename)
    rpc = rpc_specs(tree)
    ctx, pages, components = scan_module(tree, ui_all, filename, _resolver(filename, libs, stack))
    libraries = _library_order(ctx.libraries)
    seen = {spec["name"]: os.path.basename(filename) for spec in rpc}
    for lib in libraries:
        for spec in lib.rpc:
            where = os.path.basename(lib.path)
            if seen.get(spec["name"], where) != where:
                raise CompileError(f"two @server functions are named {spec['name']!r} ({seen[spec['name']]} "
                                   f"and {where}); RPC names must be unique across the app",
                                   spec.get("line") or 1, filename)
            seen[spec["name"]] = where
    for comp in components:
        comp.ctx = ctx
        classify(comp, ctx)
    lock = _read_lock(filename) if ctx.npm_bindings or ctx.npm_aliases else None
    importmap = {}
    for lay in ctx.layouts.values():
        classify(lay, ctx)
    for info in pages:
        classify(info, ctx)
    emitter = Emitter(ctx)
    app_cfg = ctx.app_config
    layouts = {}
    for lay in ctx.layouts.values():
        js = emitter.page_js(lay)
        lay_map = _page_importmap(lay, ctx, lock, filename)
        importmap.update(lay_map)
        env = static_state(lay, ctx)
        version = hashlib.sha256(js.encode()).hexdigest()[:10]
        layouts[lay.name] = {
            "name": lay.name, "prefix": lay.route, "info": lay, "js": js, "importmap": lay_map,
            "js_url": f"/static/{lay.name}.js?v={version}" if js else None, "version": version if js else "0",
            "body": _renderer(ctx).render(lay.ui, env), "state": {k: env.get(k) for k in lay.sent},
            "lineno": lay.node.lineno, "dynamic": any(lay.origin[n] == "server" for n in lay.order),
        }
    artifacts = {}
    page_infos, all_signals, all_computeds, all_place, all_edges = [], {}, {}, {}, []
    for info in pages:
        js = emitter.page_js(info)
        page_map = dict(_page_importmap(info, ctx, lock, filename))
        for lay in info.layouts:
            page_map.update(layouts[lay.name]["importmap"])
        importmap.update(page_map)
        args = {p: None for p in info.params}
        env = static_state(info, ctx, args)
        body = _renderer(ctx).render(info.ui, env)
        state = {k: env.get(k) for k in info.sent}
        version = hashlib.sha256(js.encode()).hexdigest()[:10]
        js_url = f"/static/{info.name}.js?v={version}" if js else None
        page_title = info.title or app_cfg.get("title") or title
        html = page_html(name=info.name, title=page_title, body=body, state=state, js_url=js_url, importmap=page_map,
                         css_urls=app_cfg.get("stylesheets") or (), lang=app_cfg.get("lang") or "en",
                         layouts=[layouts[lay.name] for lay in info.layouts], head=_static_head(info, app_cfg),
                         nav=app_cfg.get("client_nav", True) is not False)
        signals = [n for n in info.order if info.kinds[n] == "signal"]
        computeds = {n: {"code": ast.unparse(info.inits[n]),
                         "deps": sorted(d for d in info.deps.get(n, ()) if d in info.kinds),
                         "line": info.inits[n].lineno}
                     for n in info.order if info.kinds[n] == "computed"}
        edges = [(s, "__dom__") for s in signals if s in info.ui_names]
        for n, c in computeds.items():
            edges += [(d, n) for d in c["deps"]]
            edges.append((n, "__dom__"))
        placement = dict(info.reasons)
        placement["__page__"] = ("browser+server",
                                 "server renders HTML per request; browser takes over interactivity"
                                 if js else "static HTML (no JavaScript shipped)")
        sourcemap = [(f"sig {s}", info.inits[s].lineno if s in info.inits else info.node.lineno)
                     for s in signals]
        sourcemap += [(f"handler {h}", node.lineno) for h, node in info.handlers.items()]
        initial = {n: env.get(n) for n in info.order}
        artifacts[info.name] = {
            "ui": info.ui, "signals": signals, "computeds": computeds,
            "placement": placement, "edges": edges, "initial": initial,
            "js": js, "html": html, "html_body": body, "lineno": info.node.lineno,
            "params": info.params, "sourcemap": sourcemap,
            "route": None if info.error_status else (info.route or route), "error_status": info.error_status,
            "layouts": [lay.name for lay in info.layouts], "head": dict(info.head),
            "guards": [g for g in [lay.guard for lay in info.layouts] + [info.guard] if g],
            "handlers": {h: {"line": n.lineno} for h, n in info.handlers.items()},
            "source": ast.get_source_segment(source, info.node) or "",
            "dynamic": bool(info.params) or any(info.origin[n] == "server" for n in info.order)
            or any(layouts[lay.name]["dynamic"] for lay in info.layouts),
            "state_keys": list(info.sent), "title": page_title, "info": info, "js_url": js_url,
            "importmap": page_map,
        }
        all_signals.update({s: initial.get(s) for s in signals})
        all_computeds.update(computeds)
        all_place.update(placement)
        all_edges.extend(edges)
        if not info.error_status:
            page_infos.append({"name": info.name, "route": info.route or route, "signals": signals})
    for name, callers in ctx.rpc_calls.items():
        callers = sorted(c for c in callers if not c.startswith("<"))
        if callers:
            all_place[name] = ("server", f"@server function; browser calls it over RPC from "
                                         f"{', '.join(callers)}")
    for spec in rpc:
        all_place.setdefault(spec["name"], ("server", "@server function (not called from browser code)"))
    graph = build_graph(page_infos, rpc, all_signals, all_computeds, all_place, all_edges)
    return {"graph": graph, "ir_text": to_text(graph), "rpc": rpc, "pages": artifacts, "layouts": layouts,
            "components": {c.name: c for c in components}, "context": ctx,
            "libraries": libraries, "emitter": emitter, "importmap": importmap}


def _static_head(info, app_cfg):
    """Head tags known at compile time (the server adds canonical URLs and ``head()`` values)."""
    head = {"description": app_cfg.get("description"), "image": app_cfg.get("image")}
    head.update(info.head)
    return {k: v for k, v in head.items() if v not in (None, False, "")}


def _read_lock(filename):
    """The nearest pyweb.lock, or None when compiling loose source."""
    from pyweb import packages
    if not filename or filename.startswith("<") or not os.path.isfile(filename):
        return None
    root = packages.find_lock(os.path.dirname(os.path.abspath(filename)))
    return packages.read_lock(root) if root else {}


def _page_importmap(info, ctx, lock, filename):
    from pyweb import packages
    specs = getattr(info, "npm", [])
    if not specs or lock is None:
        return {}
    for spec in specs:
        if spec not in lock.get("imports", {}):
            line = next((ln for s, _e, ln in ctx.npm_bindings.values() if s == spec), info.node.lineno)
            raise CompileError(f"npm package {spec!r} isn't installed: run `pyweb add {spec}` in the app folder "
                               "(it downloads it into static/vendor/ and records it in pyweb.lock)", line, filename)
    return packages.page_imports(lock, specs)


def _library_order(libs):
    """Imported files, dependencies first, each once."""
    out = []

    def visit(lib):
        for dep in lib.libraries:
            visit(dep)
        if lib not in out:
            out.append(lib)

    for lib in libs:
        visit(lib)
    return out
