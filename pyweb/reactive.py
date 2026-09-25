"""Reactive primitives. Server-side semantics; the compiler lowers plain
locals into these automatically, so basic apps never import this module."""

from __future__ import annotations

import asyncio


class Signal:
    def __init__(self, value=None):
        self._value = value
        self._subs = []

    def __call__(self, *args):
        if args:
            self._value = args[0]
            for sub in list(self._subs):
                sub(self._value)
        return self._value

    def subscribe(self, fn):
        self._subs.append(fn)
        return lambda: self._subs.remove(fn)


class Computed:
    def __init__(self, fn):
        self._fn = fn

    def __call__(self):
        return self._fn()


class Resource:
    def __init__(self, loader):
        self._loader = loader
        self.pending = False
        self.error = None
        self.result = None

    async def load(self):
        self.pending = True
        try:
            self.result = await self._loader() if asyncio.iscoroutinefunction(self._loader) else self._loader()
        except Exception as exc:  # noqa: BLE001
            self.error = exc
        finally:
            self.pending = False
        return self.result


class Effect:
    def __init__(self, fn):
        self._fn = fn
        fn()


def live(query):
    return Resource(query if callable(query) else (lambda: query))
