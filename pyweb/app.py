"""Application object: page registry and configuration."""

from __future__ import annotations


class App:
    """The app: its pages, layouts, error pages and site-wide settings.

    ``base_url`` (e.g. ``"https://example.com"``) gives every page a
    canonical URL and absolute social-card links; ``description`` and
    ``image`` are the defaults for pages that don't set their own.
    ``client_nav=False`` makes links do full page loads.
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
        self.cache = cache
        self.auth = auth
        self.pages: list[tuple[str, str, dict]] = []
        self.layouts: list[tuple[str, str]] = []
        self.errors: dict[int, str] = {}
        from pyweb.plugins import Registry
        self.plugins = Registry()
        for plugin in plugins:
            self.use(plugin)

    def page(self, route, *, title=None, render="server", description=None, image=None, canonical=None,
             noindex=False, layout=...):
        """Register a page at ``route``. ``{name}`` segments and other parameters of the
        function (from the query string) become its arguments."""
        def deco(fn):
            self.pages.append((route, fn.__name__, {"render": render, "title": title}))
            fn.__pyweb_route__ = route
            fn.__pyweb_title__ = title
            fn.__pyweb_render__ = render
            return fn

        return deco

    def layout(self, prefix="/"):
        """Wrap every page under ``prefix`` in this function's markup (``{children}`` is the page).

        Use as ``@app.layout`` or ``@app.layout("/admin")``.
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

    def use(self, plugin):
        """Attach a :class:`pyweb.plugins.Plugin`; merges its routes."""
        self.plugins.add(plugin)
        for route, fn in plugin.routes:
            self.pages.append((route, fn.__name__, {"render": "server",
                                                   "plugin": plugin.name}))
        return plugin
