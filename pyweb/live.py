"""Live queries: dependency-tracked subscriptions with minimal patches.

A ``LiveTable`` wraps rows and a ``Bus`` channel. ``live_query`` records
which rows a selector reads; ``commit`` diffs versions and only notifies
subscribers whose dependency set intersects the changed rows.
"""

from __future__ import annotations

import threading

from .realtime import Bus


class LiveTable:
    def __init__(self, name, bus=None):
        self.name = name
        self.bus = bus or Bus()
        self._rows: dict[str, dict] = {}
        self._versions: dict[str, int] = {}
        self._lock = threading.Lock()

    def set(self, row_id, data):
        with self._lock:
            self._rows[row_id] = dict(data)
            self._versions[row_id] = self._versions.get(row_id, 0) + 1
            version = self._versions[row_id]
        self.bus.publish(f"live:{self.name}", {"row": row_id, "version": version})
        return version

    def delete(self, row_id):
        with self._lock:
            self._rows.pop(row_id, None)
            self._versions[row_id] = self._versions.get(row_id, 0) + 1
        self.bus.publish(f"live:{self.name}", {"row": row_id, "deleted": True})

    def read(self, row_id, tracker=None):
        with self._lock:
            row = dict(self._rows.get(row_id, {}))
        if tracker is not None:
            tracker.add(row_id)
        return row

    def all(self, tracker=None):
        with self._lock:
            rows = {k: dict(v) for k, v in self._rows.items()}
        if tracker is not None:
            tracker.update(rows.keys())
        return rows


class LiveQuery:
    """A selector + subscriber set; only fires when deps change."""

    def __init__(self, table, selector):
        self.table = table
        self.selector = selector
        self.deps: set[str] = set()
        self.value = None
        self.fires = 0
        self._subs: list = []
        self.refresh()
        table.bus.channel(f"live:{table.name}").subscribe(self._on_change)

    def refresh(self):
        tracker: set[str] = set()
        import inspect
        try:
            if len(inspect.signature(self.selector).parameters) >= 2:
                self.value = self.selector(self.table, tracker)
            else:
                self.value = self.selector(tracker)
        except TypeError:
            self.value = self.selector()
        self.deps = set(tracker)
        return self.value

    def _on_change(self, msg):
        if not self.deps or msg.get("row") in self.deps:
            self.refresh()
            self.fires += 1
            for sub in list(self._subs):
                sub(self.value)

    def subscribe(self, fn):
        self._subs.append(fn)
        fn(self.value)
        return lambda: self._subs.remove(fn)
