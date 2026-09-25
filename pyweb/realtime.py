"""Realtime: channels/rooms, pub-sub fanout, presence, live-query bus."""

from __future__ import annotations

import threading


class Channel:
    def __init__(self, name):
        self.name = name
        self._subs: list = []
        self._presence: dict[str, dict] = {}
        self._lock = threading.Lock()

    def subscribe(self, fn):
        with self._lock:
            self._subs.append(fn)
        return lambda: self.unsubscribe(fn)

    def unsubscribe(self, fn):
        with self._lock:
            if fn in self._subs:
                self._subs.remove(fn)

    def publish(self, message):
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            sub(message)
        return len(subs)

    def join(self, client_id, info=None):
        with self._lock:
            self._presence[client_id] = info or {}
            return len(self._presence)

    def leave(self, client_id):
        with self._lock:
            self._presence.pop(client_id, None)

    def members(self):
        with self._lock:
            return dict(self._presence)


class Bus:
    def __init__(self):
        self.channels: dict[str, Channel] = {}
        self._lock = threading.Lock()

    def channel(self, name):
        with self._lock:
            if name not in self.channels:
                self.channels[name] = Channel(name)
            return self.channels[name]

    def publish(self, channel_name, message):
        return self.channel(channel_name).publish(message)

    def notify_table(self, table, row=None):
        """Fanout for live DB queries watching a table."""
        return self.publish(f"db:{table}", {"table": table, "row": row})


_default_bus = Bus()


def realtime(fn=None, *, channel=None):
    def deco(f):
        f.__pyweb_realtime__ = channel or f.__name__
        return f
    return deco(fn) if fn else deco
