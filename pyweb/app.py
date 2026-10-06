"""Application object: page registry and configuration."""

from __future__ import annotations


class App:
    """The app: its pages, layouts, error pages and site-wide settings.

    ``base_url`` (e.g. ``"https://example.com"``) gives every page a
    canonical URL and absolute social-card links; ``description`` and
    ``image`` are the defaults for pages that don't set their own.
    ``client_nav=False`` makes links do full page loads.
    ``database`` (a URL like ``"sqlite:///app.db"`` or a database object) is
    where Models keep their rows; without it, ``DATABASE_URL`` is used.
    """

    def __init__(self, database=None, cache=None, auth=None, plugins=(), *,
                 title="PyWeb", stylesheets=(), lang="en", base_url=None, description=None,
                 image=None, client_nav=True):
        self.title = title
        self.stylesheets = list(stylesheets)
        self.lang = lang
        self.base_url = base_url.rstrip("/") if base_url else None
        self.description = description
        self.image = image
        self.client_nav = client_nav
        self.database = database
        self.db = None
        if database is not None:
            import os as _os
            from pyweb import models as _models
            if isinstance(database, str):
                from pyweb.db import connect
                replica = _os.environ.get("DATABASE_REPLICA_URL") or None
                database = connect(database, replica=replica)
            self.db = _models.use_database(database)
        self.cache = cache
        self.auth = auth
        self.models: list = []           # Models added by plugins (use_auth) for migrations
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
             noindex=False, layout=..., login=False, roles=(), fresh=None):
        """Register a page at ``route``. ``{name}`` segments and other parameters of the
        function (from the query string) become its arguments.

        ``login=True`` sends visitors who aren't signed in to ``/login`` first;
        ``roles=["admin"]`` also requires those roles (403 otherwise);
        ``fresh=600`` requires a sign-in in the last 600 seconds.
        """
        def deco(fn):
            self.pages.append((route, fn.__name__, {"render": render, "title": title}))
            fn.__pyweb_route__ = route
            fn.__pyweb_title__ = title
            fn.__pyweb_render__ = render
            return fn

        return deco

    def layout(self, prefix="/", *, login=False, roles=(), fresh=None):
        """Wrap every page under ``prefix`` in this function's markup (``{children}`` is the page).

        Use as ``@app.layout`` or ``@app.layout("/admin")``. ``login=``, ``roles=``
        and ``fresh=`` (as on :meth:`page`) then guard every page under ``prefix``.
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

    def use_auth(self, db=None, **options):
        """Accounts and sign-in: see :mod:`pyweb.authkit`. Returns the kit (``auth.user()`` ...)."""
        from pyweb.authkit import use_auth
        return use_auth(self, db, **options)

    def use(self, plugin):
        """Attach a :class:`pyweb.plugins.Plugin`; merges its routes."""
        self.plugins.add(plugin)
        for route, fn in plugin.routes:
            self.pages.append((route, fn.__name__, {"render": "server",
                                                   "plugin": plugin.name}))
        return plugin
