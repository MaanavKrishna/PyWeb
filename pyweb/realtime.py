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


class RedisBus(Bus):
    """Cross-process fanout over Redis pub/sub + streams.

    Local subscribers fire synchronously (same API as :class:`Bus`);
    every publish is also ``XADD``-ed to a per-channel stream
    (``pyweb:stream:<channel>``) so other processes/hosts can catch up
    via ``since()``, and ``PUBLISH``-ed for live wakeups. ``since()``
    merges the local log with the Redis stream. Without ``redis``
    installed (or when the server is unreachable) it degrades to the
    in-memory :class:`Bus` instead of raising at import time; the
    error surfaces on first publish.

    Why both stream + pubsub: streams give durable history/resume,
    pubsub gives low-latency wakeups without polling. Alternatives
    considered: Postgres LISTEN/NOTIFY (no history), bare pubsub
    (no resume after disconnect).
    """

    def __init__(self, url="redis://localhost:6379/0", client=None,
                 prefix="pyweb:"):
        super().__init__()
        self._prefix = prefix
        if client is not None:
            self._r = client
        else:
            try:
                import redis
            except ImportError as e:
                raise RuntimeError(
                    "RedisBus requires the 'redis' package: "
                    "pip install redis") from e
            self._r = redis.Redis.from_url(url)
        self._broken = None

    def _stream_key(self, channel_name):
        return f"{self._prefix}stream:{channel_name}"

    def _pub_key(self, channel_name):
        return f"{self._prefix}live:{channel_name}"

    def _seq_key(self, channel_name):
        return f"{self._prefix}seq:{channel_name}"

    def publish(self, channel_name, message):
        """Publish to local subscribers and to Redis.

        Sequence numbers come from a per-channel Redis counter and double
        as stream IDs (``<seq>-1``), so every process agrees on ids and
        ``since(last_id)`` resumes exactly where a client left off.
        """
        import json as _json
        if self._broken:
            return super().publish(channel_name, message)
        try:
            body = message if isinstance(message, str) else _json.dumps(message)
            seq = int(self._r.incr(self._seq_key(channel_name)))
            self._r.xadd(self._stream_key(channel_name), {"body": body},
                         id=f"{seq}-1", maxlen=1000, approximate=True)
            self._r.publish(self._pub_key(channel_name), body)
        except Exception as exc:  # noqa: BLE001 — degrade, don't crash app
            self._broken = exc
            return super().publish(channel_name, message)
        with self._lock:
            self._seq = max(self._seq, seq)
            log = self._log.setdefault(channel_name, [])
            log.append((seq, message))
            self._log[channel_name] = log[-100:]
        return self.channel(channel_name).publish(message)

    def since(self, channel_name, last_id=0, limit=50):
        """Messages after ``last_id`` from the shared Redis stream."""
        if self._broken:
            return super().since(channel_name, last_id, limit)
        try:
            import json as _json
            entries = self._r.xrange(self._stream_key(channel_name),
                                     min=f"({int(last_id)}-1", max="+", count=limit)
            out = []
            for seq_raw, fields in entries:
                sid = seq_raw.decode() if isinstance(seq_raw, bytes) else seq_raw
                seq = int(sid.split("-")[0])
                raw = fields.get(b"body", fields.get("body", b""))
                body = raw.decode() if isinstance(raw, bytes) else raw
                try:
                    out.append((seq, _json.loads(body)))
                except (ValueError, TypeError):
                    out.append((seq, body))
            return out
        except Exception as exc:  # noqa: BLE001
            self._broken = exc
            return super().since(channel_name, last_id, limit)

    def history(self, channel_name, limit=50):
        """Newest-first convenience wrapper used by chat/presence UIs."""
        return list(reversed(self.since(channel_name, 0, limit)))
