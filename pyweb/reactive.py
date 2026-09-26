"""Python-side reactive primitives shared by SSR and the pure-Python API."""
from __future__ import annotations

from typing import Any, Callable

_TRACKING: list["Computed | _Effect"] = []


class Signal:
    def __init__(self, initial: Any = None, name: str = ""):
        self._value = initial
        self._subs: list[Callable[[], None]] = []
        self._version = 0
        self.name = name

    def get(self) -> Any:
        if _TRACKING:
            _TRACKING[-1]._track(self)
        return self._value

    def set(self, value: Any) -> None:
        self._value = value
        self._version += 1
        for sub in list(self._subs):
            sub()

    def update(self, fn: Callable[[Any], Any]) -> None:
        self.set(fn(self.get()))

    def subscribe(self, fn: Callable[[], None]) -> Callable[[], None]:
        self._subs.append(fn)

        def unsub() -> None:
            if fn in self._subs:
                self._subs.remove(fn)

        return unsub

    @property
    def value(self) -> Any:
        return self.get()

    @value.setter
    def value(self, v: Any) -> None:
        self.set(v)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"Signal({self._value!r})"


def signal(initial: Any = None, name: str = "") -> Signal:
    return Signal(initial, name)


class Computed:
    def __init__(self, fn: Callable[[], Any], name: str = ""):
        self._fn = fn
        self.name = name
        self._cached: Any = None
        self._deps: list[Signal | Computed] = []
        self._unsubs: list[Callable[[], None]] = []
        self._dirty = True
        self._subs: list[Callable[[], None]] = []
        self._recompute()

    def _track(self, dep: "Signal | Computed") -> None:
        if dep not in self._deps:
            self._deps.append(dep)
            self._unsubs.append(dep.subscribe(self._invalidate))

    def _invalidate(self) -> None:
        self._dirty = True
        for sub in list(self._subs):
            sub()

    def _recompute(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._deps = []
        self._unsubs = []
        _TRACKING.append(self)
        try:
            self._cached = self._fn()
        finally:
            _TRACKING.pop()
        self._dirty = False

    def get(self) -> Any:
        if _TRACKING:
            _TRACKING[-1]._track(self)
        if self._dirty:
            self._recompute()
        return self._cached

    def subscribe(self, fn: Callable[[], None]) -> Callable[[], None]:
        self._subs.append(fn)

        def unsub() -> None:
            if fn in self._subs:
                self._subs.remove(fn)

        return unsub

    @property
    def value(self) -> Any:
        return self.get()


def computed(fn: Callable[[], Any], name: str = "") -> Computed:
    return Computed(fn, name)


class _Effect:
    def __init__(self, fn: Callable[[], None]):
        self._fn = fn
        self._deps: list[Signal | Computed] = []
        self._unsubs: list[Callable[[], None]] = []
        self.run()

    def _track(self, dep: "Signal | Computed") -> None:
        if dep not in self._deps:
            self._deps.append(dep)
            self._unsubs.append(dep.subscribe(self.run))

    def run(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._deps = []
        self._unsubs = []
        _TRACKING.append(self)
        try:
            self._fn()
        finally:
            _TRACKING.pop()

    def dispose(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []


def effect(fn: Callable[[], None]) -> Callable[[], None]:
    eff = _Effect(fn)
    return eff.dispose
