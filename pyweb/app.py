"""Application object: page registry and configuration."""

from __future__ import annotations


class App:
    def __init__(self, database=None, cache=None, auth=None):
        self.database = database
        self.cache = cache
        self.auth = auth
        self.pages: list[tuple[str, str, dict]] = []

    def page(self, route, *, render="server"):
        def deco(fn):
            self.pages.append((route, fn.__name__, {"render": render}))
            fn.__pyweb_route__ = route
            fn.__pyweb_render__ = render
            return fn

        return deco
