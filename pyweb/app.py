"""Application object: page registry and configuration."""

from __future__ import annotations


class App:
    def __init__(self, database=None, cache=None, auth=None, plugins=()):
        self.database = database
        self.cache = cache
        self.auth = auth
        self.pages: list[tuple[str, str, dict]] = []
        from pyweb.plugins import Registry
        self.plugins = Registry()
        for plugin in plugins:
            self.use(plugin)

    def page(self, route, *, render="server"):
        def deco(fn):
            self.pages.append((route, fn.__name__, {"render": render}))
            fn.__pyweb_route__ = route
            fn.__pyweb_render__ = render
            return fn

        return deco

    def use(self, plugin):
        """Attach a :class:`pyweb.plugins.Plugin`; merges its routes."""
        self.plugins.add(plugin)
        for route, fn in plugin.routes:
            self.pages.append((route, fn.__name__, {"render": "server",
                                                   "plugin": plugin.name}))
        return plugin
