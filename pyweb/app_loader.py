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
from .ssr import Renderer, page_html

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
        self.module = self._exec()
        self.rpc = {}
        for spec in self.compiled["rpc"]:
            fn = self.module.__dict__.get(spec["name"])
            if callable(fn):
                self.rpc[spec["name"]] = fn
        app_obj = next((v for v in self.module.__dict__.values()
                        if type(v).__name__ == "App" and type(v).__module__.startswith("pyweb")), None)
        self.app = app_obj
        self.title = getattr(app_obj, "title", None) or "PyWeb"
        self.stylesheets = list(getattr(app_obj, "stylesheets", []) or []) + self.extra_stylesheets
        self.lang = getattr(app_obj, "lang", "en") or "en"

    # ----------------------------------------------------------- module
    def _exec(self):
        py_source, _ui = P.split_sources(self.source)
        tree = ast.parse(py_source, filename=self.filename)
        infos = [p["info"] for p in self.compiled["pages"].values()]
        infos += list(self.compiled["components"].values())
        for info in infos:
            tree.body.append(_state_function(info.node))
        stem = os.path.splitext(os.path.basename(self.filename))[0] or "app"
        mod = types.ModuleType(f"pyweb_app_{stem}")
        mod.__file__ = os.path.abspath(self.filename) if self.path else self.filename
        mod.__dict__["__pyweb_ui__"] = lambda *_a: None
        app_dir = os.path.dirname(os.path.abspath(self.path)) if self.path else None
        if app_dir and app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        sys.modules[mod.__name__] = mod
        exec(compile(tree, self.filename, "exec"), mod.__dict__)  # noqa: S102 - the app itself
        return mod

    # -------------------------------------------------------- rendering
    def _component_state(self, name, props):
        info = self.compiled["components"][name]
        fn = self.module.__dict__[STATE_PREFIX + name]
        env = fn(**{k: v for k, v in props.items() if k in info.params})
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
                         lang=self.lang)
