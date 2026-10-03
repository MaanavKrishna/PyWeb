"""Load a `.pyweb` app for serving: compile, execute the Python half,
wire ``@server`` functions to RPC, and render pages per request.

``pyweb dev``, ``pyweb serve`` and the ASGI adapter all go through
:class:`LoadedApp`, so the three behave identically.
"""

from __future__ import annotations

import ast
import copy
import inspect
import os
import sys
import types

from .compiler import compile_source
from .compiler import parser as P
from .compiler.lower import is_ui_stmt
from .context import NotFound, Redirect
from .ssr import NS_KEY, Renderer, page_html

STATE_PREFIX = "__pyweb_state_"


def _state_function(fn):
    """Copy a page/component function into one that returns its locals.

    Markup placeholders are dropped; an explicit ``return`` (e.g.
    ``return redirect("/login")``) still returns early.
    """
    new = copy.deepcopy(fn)
    new.name = STATE_PREFIX + fn.name
    new.decorator_list = []
    body = [s for s in new.body if not is_ui_stmt(s)]
    ret = ast.Return(value=ast.Call(func=ast.Name(id="locals", ctx=ast.Load()), args=[], keywords=[]))
    body.append(ret)
    new.body = body
    ast.fix_missing_locations(new)
    return new


class LoadedApp:
    def __init__(self, path=None, *, source=None, filename=None, asset_urls=None,
                 stylesheets=None):
        if source is None:
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
        self.path = path
        self.filename = filename or path or "app.pyweb"
        self.source = source
        self.asset_urls = dict(asset_urls or {})
        self.extra_stylesheets = list(stylesheets or [])
        self.compiled = compile_source(source, filename=self.filename)
        self.ctx = self.compiled["context"]
        # Other .pyweb files the app imports run as real modules, registered
        # under their import name so `from widgets import Card` works.
        self.libraries = self.compiled["libraries"]
        self.lib_modules = {}
        for lib in self.libraries:
            with open(lib.path, encoding="utf-8") as fh:
                lib_source = fh.read()
            infos = list(lib.components.values())
            self.lib_modules[lib.path] = self._exec(lib_source, lib.path, infos, lib.name)
        self.files = [os.path.abspath(path)] if path else []
        self.files += [lib.path for lib in self.libraries]
        infos = [p["info"] for p in self.compiled["pages"].values()]
        infos += list(self.compiled["components"].values())
        self.module = self._exec(source, self.filename, infos)
        self.rpc = {}
        for spec in self.compiled["rpc"]:
            fn = self.module.__dict__.get(spec["name"])
            if callable(fn):
                self.rpc[spec["name"]] = fn
        for lib in self.libraries:
            for spec in lib.rpc:
                fn = self.lib_modules[lib.path].__dict__.get(spec["name"])
                if callable(fn):
                    self.rpc[spec["name"]] = fn
        app_obj = next((v for v in self.module.__dict__.values()
                        if type(v).__name__ == "App" and type(v).__module__.startswith("pyweb")), None)
        self.app = app_obj
        self.title = getattr(app_obj, "title", None) or "PyWeb"
        self.stylesheets = list(getattr(app_obj, "stylesheets", []) or []) + self.extra_stylesheets
        self.lang = getattr(app_obj, "lang", "en") or "en"

    # ----------------------------------------------------------- module
    def _exec(self, source, filename, infos, module_name=None):
        py_source, _ui = P.split_sources(source)
        tree = ast.parse(py_source, filename=filename)
        for info in infos:
            tree.body.append(_state_function(info.node))
        stem = os.path.splitext(os.path.basename(filename))[0] or "app"
        mod = types.ModuleType(module_name or f"pyweb_app_{stem}")
        mod.__file__ = os.path.abspath(filename) if self.path else filename
        mod.__dict__["__pyweb_ui__"] = lambda *_a: None
        app_dir = os.path.dirname(os.path.abspath(self.path)) if self.path else None
        if app_dir and app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        sys.modules[mod.__name__] = mod
        exec(compile(tree, filename, "exec"), mod.__dict__)  # noqa: S102 - the app itself
        return mod

    # -------------------------------------------------------- rendering
    def _component_state(self, name, props, ns=None):
        """Run component ``name`` as used by the file ``ns`` (None: the app)."""
        ctx = ns or self.ctx
        source = ctx.imported_components.get(name)
        if source is not None:
            lib, name = source
            ctx, module = lib.ctx, self.lib_modules[lib.path]
        else:
            module = self.module if ctx is self.ctx else next(
                self.lib_modules[lib.path] for lib in self.libraries if lib.ctx is ctx)
        info = ctx.components[name]
        fn = module.__dict__[STATE_PREFIX + name]
        local = fn(**{k: v for k, v in props.items() if k in info.params})
        if module is self.module:
            return info.ui, local
        # Markup in another file sees that file's globals.
        env = {k: v for k, v in module.__dict__.items() if not k.startswith("__")}
        env.update(local)
        env[NS_KEY] = ctx
        return info.ui, env

    def renderer(self):
        return Renderer(self.module.__dict__, component_state=self._component_state, strict=True)

    def coerce_params(self, page_name, raw):
        fn = self.module.__dict__[STATE_PREFIX + page_name]
        out = {}
        for pname, param in inspect.signature(fn).parameters.items():
            if pname not in raw:
                continue
            ann = param.annotation
            value = raw[pname]
            try:
                if ann in (int, "int"):
                    value = int(value)
                elif ann in (float, "float"):
                    value = float(value)
            except ValueError:
                raise NotFound(f"{pname}={value!r}") from None
            out[pname] = value
        return out

    def render(self, page_name, params=None):
        """Return ``html`` (str) or a :class:`~pyweb.context.Redirect`."""
        page = self.compiled["pages"][page_name]
        info = page["info"]
        fn = self.module.__dict__[STATE_PREFIX + page_name]
        args = self.coerce_params(page_name, params or {})
        result = fn(**args)
        if isinstance(result, Redirect):
            return result
        if not isinstance(result, dict):
            raise TypeError(f"page {page_name}() returned {type(result).__name__}; pages may only "
                            "return redirect(...)")
        env = dict(result)
        body = self.renderer().render(info.ui, env)
        state = {k: env.get(k) for k in info.sent}
        js_url = self.asset_urls.get(page_name)
        if js_url is None and page["js"]:
            js_url = page["js_url"]
        title = info.title or self.title
        if callable(getattr(self.app, "title_for", None)):
            title = self.app.title_for(page_name) or title
        return page_html(name=page_name, title=title, body=body, state=state,
                         js_url=js_url if page["js"] else None, css_urls=self.stylesheets,
                         lang=self.lang, importmap=page.get("importmap"))
