"""Typed browser API stubs (server-safe no-ops; compiler binds real impls)."""

from __future__ import annotations


class _Storage:
    _d: dict = {}

    def __getitem__(self, k):
        return self._d.get(k)

    def __setitem__(self, k, v):
        self._d[k] = v


storage = _Storage()


class _Clipboard:
    async def write(self, text: str):
        return True


clipboard = _Clipboard()


class _Location:
    async def current(self):
        return {"lat": 0.0, "lng": 0.0}


location = _Location()
