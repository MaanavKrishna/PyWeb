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
        self._seq = 0
        self._log: dict[str, list[tuple[int, object]]] = {}

    def channel(self, name):
        with self._lock:
            if name not in self.channels:
                self.channels[name] = Channel(name)
            return self.channels[name]

    def publish(self, channel_name, message):
        with self._lock:
            self._seq += 1
            entry = (self._seq, message)
            self._log.setdefault(channel_name, []).append(entry)
            self._log[channel_name] = self._log[channel_name][-100:]
        return self.channel(channel_name).publish(message)

    def since(self, channel_name, last_id=0, limit=50):
        """Messages on a channel after last_id (poll fallback)."""
        with self._lock:
            entries = [e for e in self._log.get(channel_name, []) if e[0] > last_id]
        return entries[:limit]

    def notify_table(self, table, row=None):
        """Fanout for live DB queries watching a table."""
        return self.publish(f"db:{table}", {"table": table, "row": row})


def sse_format(seq, channel_name, message):
    """One SSE frame: id/event/data lines. Client filters by event name."""
    import json as _json
    data = message if isinstance(message, str) else _json.dumps(message)
    return f"id: {seq}\nevent: {channel_name}\ndata: {data}\n\n"


_default_bus = Bus()


def realtime(fn=None, *, channel=None):
    def deco(f):
        f.__pyweb_realtime__ = channel or f.__name__
        return f
    return deco(fn) if fn else deco
