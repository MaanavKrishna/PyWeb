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
import typing

from .compiler import compile_source
from .compiler import parser as P
from .compiler.lower import is_ui_stmt
from .context import BadRequest, NotFound, Redirect, _page_head
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
        infos += [lay["info"] for lay in self.compiled.get("layouts", {}).values()]
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
        if self.ctx.live_names or any(lib.ctx.live_names for lib in self.libraries):
            from . import livedata
            livedata.enable()            # announce table writes from this process, even before a live() call
        self.migrations_dir = os.path.join(os.path.dirname(os.path.abspath(self.path)), "migrations") \
            if self.path else None
        self.migrated = self._prepare_database()
        self.title = getattr(app_obj, "title", None) or "PyWeb"
        self.stylesheets = list(getattr(app_obj, "stylesheets", []) or []) + self.extra_stylesheets
        self.lang = getattr(app_obj, "lang", "en") or "en"

    def models(self):
        """The Models this app uses: defined in its files, imported by them (one module
        deep), added by plugins (``app.models``), plus every Model those point at."""
        from . import models as M
        found = []

        def add(cls):
            if cls not in found and not cls._meta.abstract:
                found.append(cls)

        def scan(ns, depth):
            for value in list(ns.values()):
                if isinstance(value, type) and issubclass(value, M.Model) and value is not M.Model:
                    if value.__dict__.get("_pyweb_meta") is not None:
                        add(value)
                elif depth and isinstance(value, types.ModuleType) and not value.__name__.startswith(("pyweb", "_")):
                    scan(value.__dict__, depth - 1)
        scan(self.module.__dict__, 1)
        for mod in self.lib_modules.values():
            scan(mod.__dict__, 1)
        for cls in getattr(self.app, "models", None) or ():
            add(cls)
        return M._closure(found)

    def _prepare_database(self):
        """While developing: apply pending migrations, or (with no ``migrations/``
        folder yet) create tables as Models are first used. Production servers
        migrate only when asked (``pyweb serve --migrate``)."""
        from . import models as M
        from .config import settings
        if settings().production or os.environ.get("PYWEB_NO_AUTO_MIGRATE") or not self.models():
            return []
        db = M.database()
        if db is None:
            return []                      # Models fall back to in-memory SQLite (made on first use)
        from .db import migrate as _mig
        if self.migrations_dir and _mig.discover(self.migrations_dir):
            return _mig.upgrade(db, self.migrations_dir)
        M._auto.setdefault(db, set())
        return []

    def migrate(self, *, contract=False):
        """Apply pending migrations (``pyweb serve --migrate``). Returns their labels."""
        from . import models as M
        from .db import migrate as _mig
        db = M.database()
        if db is None or not self.migrations_dir or not os.path.isdir(self.migrations_dir):
            return []
        return _mig.upgrade(db, self.migrations_dir, contract=contract)

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

    def renderer(self, path=None):
        return Renderer(self.module.__dict__, component_state=self._component_state, strict=True, path=path)

    def coerce_params(self, page_name, raw, query=None):
        """Arguments for page ``page_name``: route segments from ``raw`` (bad values are a 404) and,
        when ``query`` (``{name: [values]}``) is given, the other parameters from the query string
        (missing or bad values are a 400)."""
        fn = self.module.__dict__[STATE_PREFIX + page_name]
        out = {}
        for pname, param in inspect.signature(fn).parameters.items():
            ann = param.annotation
            if pname in raw:
                try:
                    out[pname] = _convert(raw[pname], ann)
                except ValueError:
                    raise NotFound(f"{pname}={raw[pname]!r}") from None
            elif query is not None and pname in query:
                values = query[pname]
                try:
                    out[pname] = [_convert(v, _item_type(ann)) for v in values] if _is_list(ann) \
                        else _convert(values[-1], ann)
                except ValueError:
                    raise BadRequest(f"?{pname}={values[-1]!r} isn't a valid {_type_name(ann)}") from None
            elif query is not None and param.default is inspect.Parameter.empty:
                raise BadRequest(f"missing ?{pname}=")
        return out

    def _layout(self, name, renderer):
        """Run layout ``name``; returns its page_html entry or a Redirect."""
        lay = self.compiled["layouts"][name]
        fn = self.module.__dict__[STATE_PREFIX + name]
        result = fn(**({"children": None} if "children" in inspect.signature(fn).parameters else {}))
        if isinstance(result, Redirect):
            return result
        env = dict(result)
        js_url = (self.asset_urls.get(name) or lay["js_url"]) if lay["js"] else None
        return {"name": name, "body": renderer.render(lay["info"].ui, env), "js_url": js_url,
                "state": _state(lay["info"], env), "version": lay["version"]}

    def render(self, page_name, params=None, *, query=None, path=None, extra=None):
        """Return ``html`` (str) or a :class:`~pyweb.context.Redirect`.

        ``params`` are route segments, ``query`` the parsed query string
        (``{name: [values]}``), ``path`` the request path (for canonical URLs
        and marking current links), ``extra`` values offered to parameters of
        the same name without conversion (error pages get ``status``, ``path``,
        ``message``).
        """
        page = self.compiled["pages"][page_name]
        info = page["info"]
        fn = self.module.__dict__[STATE_PREFIX + page_name]
        token = _page_head.set({})
        try:
            args = self.coerce_params(page_name, params or {}, query)
            if extra:
                wanted = inspect.signature(fn).parameters
                args.update({k: v for k, v in extra.items() if k in wanted and k not in args})
            renderer = self.renderer(path)
            layouts = []
            for name in page.get("layouts") or ():
                entry = self._layout(name, renderer)
                if isinstance(entry, Redirect):
                    return entry
                layouts.append(entry)
            result = fn(**args)
            if isinstance(result, Redirect):
                return result
            if not isinstance(result, dict):
                raise TypeError(f"page {page_name}() returned {type(result).__name__}; pages may only "
                                "return redirect(...)")
            env = dict(result)
            body = renderer.render(info.ui, env)
            dynamic = _page_head.get()
        finally:
            _page_head.reset(token)
        state = _state(info, env)
        js_url = self.asset_urls.get(page_name)
        if js_url is None and page["js"]:
            js_url = page["js_url"]
        title = info.title or self.title
        if callable(getattr(self.app, "title_for", None)):
            title = self.app.title_for(page_name) or title
        title = dynamic.pop("title", None) or title
        importmap = dict(page.get("importmap") or {})
        return page_html(name=page_name, title=title, body=body, state=state,
                         js_url=js_url if page["js"] else None, css_urls=self.stylesheets,
                         lang=self.lang, importmap=importmap, layouts=layouts,
                         head=self.head_for(page, dynamic, path),
                         nav=getattr(self.app, "client_nav", True) is not False)

    def head_for(self, page, dynamic, path):
        """Description, image and canonical URL: the page's own, then ``head()`` values, then the app's."""
        app = self.app
        base = getattr(app, "base_url", None)
        out = {"description": getattr(app, "description", None), "image": getattr(app, "image", None)}
        out.update(page.get("head") or {})
        out.update(dynamic)
        canonical = out.get("canonical")
        if canonical is None and base and path and not page.get("error_status"):
            canonical = path
        if canonical is False:
            canonical = None
        for key, value in (("canonical", canonical), ("image", out.get("image"))):
            if isinstance(value, str) and value.startswith("/") and base:
                value = base + value
            out[key] = value
        return {k: v for k, v in out.items() if v not in (None, False, "")}


def _state(info, env):
    """What the browser gets for a page or layout: its sent variables, plus how to follow live queries."""
    state = {k: env.get(k) for k in info.sent}
    for k in info.sent:
        meta = getattr(env.get(k), "live", None)
        if isinstance(meta, dict):
            state["$live:" + k] = meta
    return state


def _is_list(ann):
    return ann is list or typing.get_origin(ann) is list or (isinstance(ann, str) and ann.startswith("list"))


def _item_type(ann):
    if isinstance(ann, str):
        inner = ann[5:-1] if ann.startswith("list[") else "str"
        return inner
    args = typing.get_args(ann)
    return args[0] if args else str


def _type_name(ann):
    return ann if isinstance(ann, str) else getattr(ann, "__name__", str(ann))


def _convert(value, ann):
    """A route or query-string value as the parameter's annotated type (ValueError if it isn't one)."""
    if ann in (int, "int"):
        return int(value)
    if ann in (float, "float"):
        return float(value)
    if ann in (bool, "bool"):
        low = str(value).strip().lower()
        if low in ("1", "true", "yes", "on", ""):
            return True
        if low in ("0", "false", "no", "off"):
            return False
        raise ValueError(value)
    return value
