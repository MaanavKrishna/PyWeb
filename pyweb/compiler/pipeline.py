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


def _renderer(ctx, globals_=None, strict=False):
    from pyweb.ssr import Renderer

    def component_state(name, props):
        comp = ctx.components[name]
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
        return comp.ui, static_state(comp, ctx, args)

    return Renderer(globals_ or static_globals(ctx), component_state=component_state, strict=strict)


def compile_source(source, filename="<pyweb>", route="/", title="PyWeb"):
    try:
        return _compile(source, filename, route, title)
    except CompileError as exc:
        if not exc.filename or exc.filename == "<pyweb>":
            exc.with_file(filename)
        raise


def _compile(source, filename, route, title):
    from pyweb.ssr import page_html
    tree, ui_all, _pages = P.parse_source(source, filename)
    rpc = rpc_specs(tree)
    ctx, pages, components = scan_module(tree, ui_all, filename)
    for comp in components:
        classify(comp, ctx)
    for info in pages:
        classify(info, ctx)
    emitter = Emitter(ctx)
    artifacts = {}
    page_infos, all_signals, all_computeds, all_place, all_edges = [], {}, {}, {}, []
    for info in pages:
        js = emitter.page_js(info)
        args = {p: None for p in info.params}
        env = static_state(info, ctx, args)
        body = _renderer(ctx).render(info.ui, env)
        state = {k: env.get(k) for k in info.sent}
        version = hashlib.sha256(js.encode()).hexdigest()[:10]
        js_url = f"/static/{info.name}.js?v={version}" if js else None
        app_cfg = ctx.app_config
        page_title = info.title or app_cfg.get("title") or title
        html = page_html(name=info.name, title=page_title, body=body, state=state, js_url=js_url,
                         css_urls=app_cfg.get("stylesheets") or (), lang=app_cfg.get("lang") or "en")
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
            "params": info.params, "route": info.route or route, "sourcemap": sourcemap,
            "handlers": {h: {"line": n.lineno} for h, n in info.handlers.items()},
            "source": ast.get_source_segment(source, info.node) or "",
            "dynamic": bool(info.params) or any(info.origin[n] == "server" for n in info.order),
            "state_keys": list(info.sent), "title": page_title, "info": info, "js_url": js_url,
        }
        all_signals.update({s: initial.get(s) for s in signals})
        all_computeds.update(computeds)
        all_place.update(placement)
        all_edges.extend(edges)
        page_infos.append({"name": info.name, "route": info.route or route, "signals": signals})
    for name, callers in ctx.rpc_calls.items():
        callers = sorted(c for c in callers if not c.startswith("<"))
        if callers:
            all_place[name] = ("server", f"@server function; browser calls it over RPC from "
                                         f"{', '.join(callers)}")
    for spec in rpc:
        all_place.setdefault(spec["name"], ("server", "@server function (not called from browser code)"))
    graph = build_graph(page_infos, rpc, all_signals, all_computeds, all_place, all_edges)
    return {"graph": graph, "ir_text": to_text(graph), "rpc": rpc, "pages": artifacts,
            "components": {c.name: c for c in components}, "context": ctx}
