"""Application object: page registry and configuration."""

from __future__ import annotations


class App:
    """The app: its pages, layouts, error pages and site-wide settings.

    ``base_url`` (e.g. ``"https://example.com"``) gives every page a
    canonical URL and absolute social-card links; ``description`` and
    ``image`` are the defaults for pages that don't set their own.
    ``client_nav=False`` makes links do full page loads.
    ``pwa=True`` (or ``pwa=PWA(...)``) makes the app installable, usable offline and able
    to send push notifications (see :mod:`pyweb.pwa` and :mod:`pyweb.push`).
    ``desktop=Window(...)`` sets the window of the desktop app (``pyweb desktop``).
    ``database`` (a URL like ``"sqlite:///app.db"`` or a database object) is
    where Models keep their rows. ``DATABASE_URL``, when set, wins over a URL
    given here: develop on SQLite, deploy on Postgres, same code.
    """

    def __init__(self, database=None, cache=None, auth=None, plugins=(), *,
                 title="PyWeb", stylesheets=(), lang="en", base_url=None, description=None,
                 image=None, client_nav=True, pwa=None, desktop=None, theme=None, motion=None,
                 robots=None):
        self.title = title
        self.robots = robots             # /robots.txt: None (PyWeb's default), False (none), or the text
        self.http_routes: list = []      # @app.route (pyweb.routes.Route)
        self.middlewares: list = []      # @app.middleware, outermost first
        self.sitemap_sources: list = []  # @app.sitemap
        self.stylesheets = list(stylesheets)
        self.lang = lang
        self.base_url = base_url.rstrip("/") if base_url else None
        self.description = description
        self.image = image
        self.client_nav = client_nav
        self.pwa = pwa                   # True or pyweb.pwa.PWA(...): installable, offline, push
        if pwa is not None and pwa is not False:
            from pyweb.pwa import config as _pwa_config
            _pwa_config(self)            # bad settings fail here, not on the first request
        self.theme = theme               # pyweb.ui.Theme(...) or a preset name: the UI kit's look
        if theme is not None:
            from pyweb.ui.theme import config as _theme_config
            _theme_config(self)
        from pyweb.motion import coerce as _motion
        self.motion = _motion(motion)    # pyweb.motion.Motion(...): every animation's feel and speed
        self.desktop = desktop           # pyweb.desktop.Window(...): the window of the desktop app
        if desktop is not None:
            from pyweb.desktop import config as _desktop_config
            _desktop_config(self)
        self.database = database
        self.db = None
        if database is not None:
            import os as _os
            from pyweb import models as _models
            if isinstance(database, str):
                from pyweb.db import connect
                database = _os.environ.get("DATABASE_URL") or database   # the environment decides in production
                replica = _os.environ.get("DATABASE_REPLICA_URL") or None
                database = connect(database, replica=replica)
            self.db = _models.use_database(database)
        self.cache = cache
        self.auth = auth
        self.models: list = []           # Models added by plugins (use_auth) for migrations
        self.webhooks: dict = {}         # path -> pyweb.webhooks.Receiver
        if self.db is not None:
            # The job queue lives in the app's database: PyWeb's own tables (like the migrations
            # journal), created here and left out of the app's migrations.
            try:
                from pyweb import models as _m
                from pyweb.jobs.db import MODELS as _job_models
                _m.ensure_tables(self.db, _job_models)   # PyWeb's own tables, like the migrations journal
            except Exception:  # noqa: BLE001 - unreachable now: workers create them later
                pass
        self.pages: list[tuple[str, str, dict]] = []
        self.layouts: list[tuple[str, str]] = []
        self.errors: dict[int, str] = {}
        from pyweb.plugins import Registry
        self.plugins = Registry()
        for plugin in plugins:
            self.use(plugin)

    def page(self, route, *, title=None, render="server", description=None, image=None, canonical=None,
             noindex=False, layout=..., login=False, roles=(), fresh=None, org_role=None, feature=None):
        """Register a page at ``route``. ``{name}`` segments and other parameters of the
        function (from the query string) become its arguments.

        ``login=True`` sends visitors who aren't signed in to ``/login`` first;
        ``roles=["admin"]`` also requires those roles (403 otherwise);
        ``fresh=600`` requires a sign-in in the last 600 seconds;
        ``org_role="admin"`` (or a list) requires that role in the current organization and
        ``feature="exports"`` a plan with that feature (with ``use_auth(orgs=True)``).
        """
        def deco(fn):
            self.pages.append((route, fn.__name__, {"render": render, "title": title}))
            fn.__pyweb_route__ = route
            fn.__pyweb_title__ = title
            fn.__pyweb_render__ = render
            return fn

        return deco

    def layout(self, prefix="/", *, login=False, roles=(), fresh=None, org_role=None, feature=None):
        """Wrap every page under ``prefix`` in this function's markup (``{children}`` is the page).

        Use as ``@app.layout`` or ``@app.layout("/admin")``. ``login=``, ``roles=``,
        ``fresh=``, ``org_role=`` and ``feature=`` (as on :meth:`page`) then guard every page
        under ``prefix``.
        """
        if callable(prefix):
            self.layouts.append(("/", prefix.__name__))
            return prefix

        def deco(fn):
            self.layouts.append((prefix, fn.__name__))
            return fn

        return deco

    def error(self, status, *, title=None, layout=...):
        """Render this page for HTTP ``status`` (404, 500, ...) instead of the built-in one."""
        def deco(fn):
            self.errors[status] = fn.__name__
            return fn

        return deco

    # ------------------------------------------------------------- telemetry
    def on_error(self, fn):
        """``@app.on_error``: ``fn(error, info)`` for every unhandled error in a page, server
        function, form or job, with ``info`` holding the request id, trace id, route and user."""
        from .telemetry import on_error
        return on_error(fn)

    # ------------------------------------------------------------------ jobs
    def job(self, fn=None, **options):
        """A durable background job: ``@app.job(retries=5, timeout=300, queue="default")``,
        then ``fn.enqueue(...)``. See :mod:`pyweb.jobs`."""
        from .jobs import job
        return job(fn, **options) if fn is not None else job(**options)

    def cron(self, expr, **options):
        """Run the decorated function on a cron schedule: ``@app.cron("0 3 * * *", tz="Europe/London")``."""
        from .jobs import cron
        return cron(expr, **options)

    def every(self, **options):
        """Run the decorated function at a fixed interval: ``@app.every(minutes=5)``."""
        from .jobs import every
        return every(**options)

    # ------------------------------------------------------------- routes
    def route(self, path, *, methods=("GET",), login=False, roles=(), fresh=None, rate=None, per="user",
              tokens=False, cors=None, csrf=True, content_type=None):
        """An HTTP endpoint that isn't a page (see :mod:`pyweb.routes`): ``@app.route("/api/items/{id:int}",
        methods=["GET", "POST"])``. ``login=``/``roles=``/``fresh=``/``rate=`` guard it like a server
        function; ``tokens=True`` accepts API tokens; ``cors=["https://other.site"]`` lets those
        origins call it from the browser; ``content_type=`` is what a returned string is."""
        def deco(fn):
            from pyweb.routes import Route
            route = Route(path, fn, methods, login=login, roles=roles, fresh=fresh, rate=rate, per=per,
                          tokens=tokens, cors=cors, csrf=csrf, content_type=content_type)
            for other in self.http_routes:
                if other.path == path and set(other.methods) & set(route.methods) - {"HEAD"}:
                    raise ValueError(f"{path} {sorted(set(other.methods) & set(route.methods))} is already "
                                     f"answered by {other.name}()")
            self.http_routes.append(route)
            fn.__pyweb_http_route__ = route
            return fn
        return deco

    def get(self, path, **options):
        """``@app.get("/api/x")``: :meth:`route` for GET."""
        return self.route(path, methods=("GET",), **options)

    def post(self, path, **options):
        """``@app.post("/api/x")``: :meth:`route` for POST."""
        return self.route(path, methods=("POST",), **options)

    def middleware(self, fn):
        """``@app.middleware``: ``fn(request, call_next)`` around every request (see :mod:`pyweb.routes`)."""
        self.middlewares.append(fn)
        return fn

    def sitemap(self, fn):
        """``@app.sitemap``: ``fn()`` returns more paths (or full URLs) for ``/sitemap.xml``, e.g. one per
        blog post. Pages without parameters are listed already (needs ``App(base_url=...)``)."""
        self.sitemap_sources.append(fn)
        return fn

    def webhook(self, path, *, secret, scheme="standard", retries=5):
        """Receive webhooks at ``path`` (see :mod:`pyweb.webhooks`): the signature is checked, repeats are
        dropped and ``fn(event)`` runs as a background job. ``scheme``: ``"github"``, ``"stripe"`` or
        ``"standard"``; ``secret``: the signing secret (or ``webhooks.env_secret("NAME")``)."""
        def deco(fn):
            from pyweb.webhooks import receiver
            self.webhooks[path] = receiver(path, fn, secret=secret, scheme=scheme, retries=retries)
            return fn
        return deco

    def use_auth(self, db=None, **options):
        """Accounts and sign-in: see :mod:`pyweb.authkit`. Returns the kit (``auth.user()`` ...)."""
        from pyweb.authkit import use_auth
        return use_auth(self, db, **options)

    def use(self, plugin):
        """Attach a :class:`pyweb.plugins.Plugin`: its routes and pages answer, its server functions
        can be called, its Models join the migrations and its build steps run in ``pyweb build``."""
        self.plugins.add(plugin)
        for route, fn in plugin.routes:
            self.route(route, content_type="text/html; charset=utf-8")(fn)
        for path, fn, options in plugin.http_routes:
            self.route(path, **options)(fn)
        for model in plugin.models:
            if model not in self.models:
                self.models.append(model)
        if plugin.build_steps or plugin.browser_bindings:
            import warnings
            warnings.warn(f"plugin {plugin.name!r}: build steps and browser bindings aren't used by PyWeb yet "
                          "(routes, pages, server functions and Models are)", stacklevel=2)
        return plugin
