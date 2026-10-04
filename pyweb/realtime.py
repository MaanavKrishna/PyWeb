"""Realtime: channels/rooms, pub-sub fanout, presence, live-query bus."""

from __future__ import annotations

import collections
import threading
import time


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


#: Messages kept per channel for resuming clients, and how long a quiet channel is kept.
LOG_SIZE = 100
IDLE_CHANNEL_SECONDS = 3600


class Bus:
    def __init__(self):
        self.channels: dict[str, Channel] = {}
        self._lock = threading.Lock()
        self._seq = 0
        self._log: dict[str, collections.deque] = {}
        self._active: dict[str, float] = {}     # channel -> when it last had a message
        self._published = 0

    def _remember(self, channel_name, entry):
        """Log ``entry`` for ``since()``; now and then forget channels that went quiet. Hold the lock."""
        log = self._log.get(channel_name)
        if log is None:
            log = self._log[channel_name] = collections.deque(maxlen=LOG_SIZE)
        log.append(entry)
        now = time.monotonic()
        self._active[channel_name] = now
        self._published += 1
        if self._published % 256 == 0:
            for name, at in list(self._active.items()):
                if now - at < IDLE_CHANNEL_SECONDS:
                    continue
                chan = self.channels.get(name)
                if chan is not None and (chan._subs or chan._presence):
                    continue
                self._active.pop(name, None)
                self._log.pop(name, None)
                self.channels.pop(name, None)

    def channel(self, name):
        with self._lock:
            if name not in self.channels:
                self.channels[name] = Channel(name)
            return self.channels[name]

    def publish(self, channel_name, message):
        with self._lock:
            self._seq += 1
            self._remember(channel_name, (self._seq, message))
        return self.channel(channel_name).publish(message)

    def position(self, channel_name):
        """An id such that ``since(channel_name, id)`` returns only newer messages."""
        with self._lock:
            return self._seq

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
            self._remember(channel_name, (seq, message))
        return self.channel(channel_name).publish(message)

    def position(self, channel_name):
        if self._broken:
            return super().position(channel_name)
        try:
            return int(self._r.get(self._seq_key(channel_name)) or 0)
        except Exception as exc:  # noqa: BLE001
            self._broken = exc
            return super().position(channel_name)

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


# ------------------------------------------------------------ live updates
#
# Pages subscribe to channels through *feeds*: signed, expiring tokens
# minted on the server while rendering (``channel(name)``), so only
# visitors who were served the page can listen. The browser opens
# ``/__pyweb/events?feed=TOKEN`` (Server-Sent Events) and falls back to
# polling ``/__pyweb/poll?feed=TOKEN&since=ID``.

FEED_MAX_AGE = 24 * 3600


def use_bus(bus):
    """Make ``bus`` (e.g. a :class:`RedisBus`) the one ``publish()`` and feeds use."""
    global _default_bus
    _default_bus = bus
    return bus


def current_bus():
    return _default_bus


def _b64(raw: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    import base64
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(secret, payload):
    import hashlib
    import hmac
    key = secret.encode() if isinstance(secret, str) else secret
    return hmac.new(key, b"pyweb-feed:" + payload.encode(), hashlib.sha256).hexdigest()[:40]


def make_feed(name, secret, *, since=0, max_age=FEED_MAX_AGE, now=None):
    """A signed token for channel ``name``. Streams start after message ``since``."""
    import json as _json
    import time as _time
    expires = int((now or _time.time()) + max_age)
    payload = _b64(_json.dumps([str(name), expires, int(since)], separators=(",", ":")).encode())
    return f"{payload}.{_signature(secret, payload)}"


def read_feed(token, secret, *, now=None):
    """``(channel name, start id)`` for a valid feed token, else ``None``."""
    import hmac
    import json as _json
    import time as _time
    payload, _, sig = (token or "").partition(".")
    if not payload or not hmac.compare_digest(sig, _signature(secret, payload)):
        return None
    try:
        name, expires, since = _json.loads(_unb64(payload))
    except (ValueError, TypeError):
        return None
    if expires < (now or _time.time()):
        return None
    return name, since


def open_feed(token, secret, *, now=None):
    """The channel name a feed token grants, or ``None`` if forged or expired."""
    feed = read_feed(token, secret, now=now)
    return feed[0] if feed else None


def channel(name: str) -> str:
    """A feed for channel ``name``: call while rendering a page and hand it to
    ``subscribe()`` in browser code. Anyone served the page can listen.

    The feed remembers the channel's position, so messages published after
    the page rendered (even before the browser connects) are delivered.
    """
    from .context import _secret, current
    return make_feed(name, _secret(current()), since=_default_bus.position(name))


def publish(name: str, data=None) -> int:
    """Send ``data`` (anything JSON-serialisable) to every browser listening on ``name``."""
    from .ssr import to_jsonable
    return _default_bus.publish(name, to_jsonable(data))


def subscribe(feed, handler):  # noqa: ARG001 - the browser implementation takes these
    """Browser-only: call ``handler(message)`` for every message on ``feed``."""
    raise RuntimeError("subscribe() runs in the browser: call it from on_mount() or an event handler")


class EventStream:
    """A Server-Sent Events response body for one channel.

    Iterate it in a thread (stdlib servers) or ``async for`` over
    :meth:`aiter` (ASGI). Messages come from ``bus.since()``, so a client
    reconnecting with ``Last-Event-ID`` resumes exactly where it stopped.
    Streams end after ``max_age`` seconds; browsers reconnect on their own.
    """

    def __init__(self, bus, name, last_id=0, *, heartbeat=15.0, max_age=300.0, tick=1.0):
        import threading
        self.bus, self.name, self.last_id = bus, name, int(last_id or 0)
        self.heartbeat, self.max_age, self.tick = heartbeat, max_age, tick
        self._stop = threading.Event()

    def _poll(self):
        import json as _json
        out = []
        for seq, msg in self.bus.since(self.name, self.last_id, limit=100):
            data = _json.dumps(msg, separators=(",", ":"))
            out.append(f"id: {seq}\ndata: {data}\n\n")
            self.last_id = seq
        return "".join(out).encode()

    def snapshot(self):
        """The frames available right now, without waiting (tests, HEAD)."""
        return b"retry: 2000\n\n" + self._poll()

    def close(self):
        self._stop.set()

    def __iter__(self):
        import threading
        import time as _time
        wake = threading.Event()
        unsub = self.bus.channel(self.name).subscribe(lambda _msg: wake.set())
        try:
            yield b"retry: 2000\n\n"
            start = beat = _time.monotonic()
            while not self._stop.is_set():
                frames = self._poll()
                now = _time.monotonic()
                if frames:
                    yield frames
                    beat = now
                elif now - beat >= self.heartbeat:
                    yield b": ping\n\n"
                    beat = now
                if now - start >= self.max_age:
                    return
                wake.wait(self.tick)
                wake.clear()
        finally:
            unsub()

    async def aiter(self):
        import asyncio
        import time as _time
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        unsub = self.bus.channel(self.name).subscribe(lambda _msg: loop.call_soon_threadsafe(wake.set))
        local = type(self.bus) is Bus  # in-memory reads are instant; others may do I/O
        try:
            yield b"retry: 2000\n\n"
            start = beat = _time.monotonic()
            while not self._stop.is_set():
                frames = self._poll() if local else await asyncio.to_thread(self._poll)
                now = _time.monotonic()
                if frames:
                    yield frames
                    beat = now
                elif now - beat >= self.heartbeat:
                    yield b": ping\n\n"
                    beat = now
                if now - start >= self.max_age:
                    return
                try:
                    await asyncio.wait_for(wake.wait(), self.tick)
                except asyncio.TimeoutError:
                    pass
                wake.clear()
        finally:
            unsub()
