"""Live updates over one WebSocket per browser tab.

The browser opens ``/__pyweb/ws`` once and multiplexes every ``subscribe()``
and live query over it (Server-Sent Events open one connection each, and
browsers allow only six per site over HTTP/1.1).

Messages are JSON. From the browser::

    {"t": "sub", "s": 1, "feed": TOKEN, "live": SPEC?, "since": LAST_ID}
    {"t": "unsub", "s": 1}
    {"t": "snap", "s": 1}            # a live query missed a change: send it whole
    {"t": "join", "s": 2, "feed": ROOM_TOKEN, "info": {...}}   # presence (pyweb.presence)
    {"t": "cast", "s": 2, "d": DATA}                           # to everyone else in the room
    {"t": "ping"}

From the server::

    {"t": "m", "s": 1, "i": ID, "d": DATA}     # a message (ID resumes after a reconnect)
    {"t": "snap", "s": 1, "d": {"version", "rows"}}
    {"t": "r", "s": 1}                         # fell behind: ask for a snapshot
    {"t": "e", "s": 1, "status": 403, "error": "..."}
    {"t": "pong"} / {"t": "hb"}

Safety:

* the ``Origin`` must be this site (or listed in ``PYWEB_ALLOWED_ORIGINS``),
  so another site can't open a socket with the visitor's cookies (CSWSH);
* each feed is checked like an HTTP one, including session-bound feeds;
* the session is checked again every minute: when it's revoked (sign-out
  everywhere, password reset, role change) the socket closes with 4001;
* a client that can't keep up gets "resync" markers instead of an
  unbounded queue, and is disconnected (4008) if a send stays blocked;
* limits on subscriptions per socket, message size and message rate.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
import urllib.parse
import weakref

from pyweb import rooms as presence

MAX_SUBS = 256
MAX_MESSAGE = 64 * 1024
RATE = 50                        # messages per second a browser may send (burst 2x)
REVALIDATE = 60.0                # seconds between session checks
HEARTBEAT = 25.0                 # an application-level message keeps idle proxies from closing the socket
SEND_TIMEOUT = 30.0              # a send blocked this long means the client can't keep up
BEHIND = 20                      # more pending messages than this on a live query -> resync instead

SESSION_ENDED = 4001
TOO_SLOW = 4008
POLICY = 1008


def allowed_origin(headers, *, trust_proxy=False):
    """Whether the upgrade's ``Origin`` is this site (or an allowed one). ``headers``: lower-case names."""
    origin = headers.get("origin")
    if not origin:
        return False                         # browsers always send it on WebSocket upgrades
    extra = {o.strip().rstrip("/") for o in os.environ.get("PYWEB_ALLOWED_ORIGINS", "").split(",") if o.strip()}
    if origin.rstrip("/") in extra:
        return True
    host = (headers.get("x-forwarded-host") if trust_proxy else None) or headers.get("host", "")
    try:
        parsed = urllib.parse.urlsplit(origin)
    except ValueError:
        return False
    return bool(parsed.netloc) and parsed.netloc.lower() == host.split(",")[0].strip().lower()


class _Sub:
    __slots__ = ("sid", "bus", "name", "last", "dirty", "unsub", "live_key", "local", "member", "shown")

    def __init__(self, sid, bus, name, last):
        self.sid, self.bus, self.name, self.last = sid, bus, name, last
        self.dirty = True
        self.unsub = None
        self.live_key = None
        self.member = None                     # presence: this page's member id in the room
        self.shown = None                      # presence: the member list last sent
        from pyweb.realtime import Bus
        self.local = type(bus) is Bus          # in-memory reads never block


class LiveSession:
    """One browser's socket. Transport-agnostic: give :meth:`run` three coroutines."""

    def __init__(self, server, req, payload):
        self.server = server                   # pyweb.runtime.server.Server
        self.req = req                         # the upgrade request (its cookies are the session)
        self.payload = payload                 # the session at connect time (or None)
        self.subs: dict = {}
        self.closed = False
        self.wake = None
        self.loop = None
        self._allowance = RATE * 2.0
        self._checked = time.monotonic()

    # ------------------------------------------------------------ transport
    async def run(self, recv, send, close):
        """``recv()`` -> str | None (closed), ``send(text)``, ``close(code, reason)``."""
        self.loop = asyncio.get_running_loop()
        self.wake = asyncio.Event()
        self._send, self._close = send, close
        reader = asyncio.ensure_future(self._reader(recv))
        try:
            await self._out({"t": "hello", "v": 2})
            last_beat = time.monotonic()
            next_check = time.monotonic() + REVALIDATE
            next_renew = time.monotonic() + presence.BEAT
            while not self.closed:
                try:
                    await asyncio.wait_for(self.wake.wait(), 1.0)
                except asyncio.TimeoutError:
                    for sub in self.subs.values():
                        if not sub.local:
                            sub.dirty = True      # other processes' messages: poll the shared bus
                self.wake.clear()
                if reader.done():
                    break
                await self._flush()
                now = time.monotonic()
                if now >= next_renew:
                    next_renew = now + presence.BEAT
                    for sub in list(self.subs.values()):
                        if sub.member:
                            self._announce(sub, {"op": "beat", "id": sub.member})
                await self._show_members()
                if now - last_beat >= HEARTBEAT:
                    await self._out({"t": "hb"})
                    last_beat = now
                if now >= next_check:
                    next_check = now + REVALIDATE
                    if not await asyncio.to_thread(self._session_ok):
                        await self._end(SESSION_ENDED, "session ended")
                        break
        except _TooSlow:
            await self._end(TOO_SLOW, "too slow to keep up")
        except (ConnectionError, OSError):
            pass
        finally:
            self.closed = True
            reader.cancel()
            self._release()

    def going_away(self):
        """Close with 1001 (server shutting down): the browser reconnects, to another server if there is one."""
        if self.loop is not None and not self.closed:
            self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._end(1001, "server restarting")))

    async def _end(self, code, reason):
        if self.closed and code == 1001:
            return
        self.closed = True
        if self.wake is not None:
            self.wake.set()
        try:
            await asyncio.wait_for(self._close(code, reason), 5)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass

    async def _out(self, msg):
        text = json.dumps(msg, separators=(",", ":"), default=str)
        try:
            await asyncio.wait_for(self._send(text), SEND_TIMEOUT)
        except asyncio.TimeoutError:
            raise _TooSlow() from None

    async def _reader(self, recv):
        while not self.closed:
            text = await recv()
            if text is None:
                self.closed = True
                self.wake.set()
                return
            if not self._rate_ok():
                await self._end(POLICY, "too many messages")
                self.wake.set()
                return
            if isinstance(text, bytes) or len(text) > MAX_MESSAGE:
                await self._end(POLICY, "messages must be JSON text under 64 KB")
                self.wake.set()
                return
            try:
                msg = json.loads(text)
            except ValueError:
                msg = None
            if not isinstance(msg, dict):
                await self._end(POLICY, "messages must be JSON objects")
                self.wake.set()
                return
            await self._handle(msg)

    def _rate_ok(self):
        now = time.monotonic()
        self._allowance = min(RATE * 2.0, self._allowance + (now - self._checked) * RATE)
        self._checked = now
        if self._allowance < 1:
            return False
        self._allowance -= 1
        return True

    # ------------------------------------------------------------- messages
    async def _handle(self, msg):
        kind = msg.get("t")
        sid = msg.get("s")
        if kind == "ping":
            await self._out({"t": "pong"})
        elif kind == "sub":
            await self._subscribe(sid, msg)
        elif kind == "unsub":
            sub = self.subs.pop(sid, None)
            if sub is not None:
                self._drop(sub)
        elif kind == "leave":
            sub = self.subs.pop(sid, None)
            if sub is not None:
                self._drop(sub)
        elif kind == "snap":
            await self._snapshot(sid)
        elif kind == "join":
            await self._join(sid, msg)
        elif kind == "cast":
            sub = self.subs.get(sid)
            data = msg.get("d")
            if sub is not None and sub.member:
                if len(json.dumps(data, default=str)) > presence.MAX_CAST:
                    return await self._out({"t": "e", "s": sid, "status": 413, "error": "cast too large"})
                self._announce(sub, {"op": "cast", "id": sub.member, "d": data})

    async def _subscribe(self, sid, msg):
        if not isinstance(sid, int) or sid in self.subs:
            return await self._out({"t": "e", "s": sid, "status": 400, "error": "bad subscription id"})
        if len(self.subs) >= MAX_SUBS:
            return await self._out({"t": "e", "s": sid, "status": 429, "error": "too many subscriptions"})
        token, spec = msg.get("feed"), msg.get("live")
        if not isinstance(token, str) or (spec is not None and not isinstance(spec, str)):
            return await self._out({"t": "e", "s": sid, "status": 400, "error": "bad feed"})
        feed = await asyncio.to_thread(self.server.in_request, self.req, self.server.open_feed, token, spec)
        if len(feed) == 2:
            return await self._out({"t": "e", "s": sid, "status": feed[0], "error": feed[1]})
        bus, name, start = feed
        try:
            since = max(int(msg.get("since") or 0), start)
        except (TypeError, ValueError):
            since = start
        sub = _Sub(sid, bus, name, since)
        loop, wake = self.loop, self.wake

        def changed(_message, sub=sub):
            sub.dirty = True
            loop.call_soon_threadsafe(wake.set)

        sub.unsub = bus.channel(name).subscribe(changed)
        if spec and name.startswith("pyweb.live:"):
            from pyweb import livedata
            sub.live_key = name[len("pyweb.live:"):]
            livedata.REGISTRY.watching(sub.live_key, 1)
        self.subs[sid] = sub
        self.wake.set()

    async def _join(self, sid, msg):
        if not isinstance(sid, int) or sid in self.subs:
            return await self._out({"t": "e", "s": sid, "status": 400, "error": "bad subscription id"})
        token = msg.get("feed")
        if not isinstance(token, str):
            return await self._out({"t": "e", "s": sid, "status": 400, "error": "bad room"})
        feed = await asyncio.to_thread(self.server.in_request, self.req, self.server.open_feed, token, None)
        if len(feed) == 2 or not feed[1].startswith(presence.PREFIX):
            return await self._out({"t": "e", "s": sid, "status": 403, "error": "invalid room; reload the page"})
        try:
            info = presence.clean_info(msg.get("info"), (self.payload or {}).get("sub"))
        except ValueError as exc:
            return await self._out({"t": "e", "s": sid, "status": 413, "error": str(exc)})
        await self._subscribe(sid, {"feed": token})
        sub = self.subs.get(sid)
        if sub is None:
            return
        sub.member = secrets.token_urlsafe(9)
        self._announce(sub, {"op": "join", "id": sub.member, "info": info})

    def _announce(self, sub, event):
        sub.bus.publish(sub.name, event)

    async def _show_members(self):
        for sub in list(self.subs.values()):
            if sub.member:
                members = presence.ROOMS.members(sub.name)
                if members != sub.shown:
                    sub.shown = members
                    await self._out({"t": "m", "s": sub.sid, "i": sub.last, "d": {"members": members}})

    async def _snapshot(self, sid):
        sub = self.subs.get(sid)
        if sub is None or sub.live_key is None:
            return
        from pyweb import livedata
        entry = livedata.REGISTRY.entries.get(sub.live_key)
        if entry is None:
            return await self._out({"t": "e", "s": sid, "status": 410, "error": "reload the page"})

        def run():
            if entry.dirty or entry.rows is None:
                entry.run()
            return {"version": entry.version, "rows": entry.rows, "key": entry.row_key}

        try:
            snap = await asyncio.to_thread(run)
        except Exception:  # noqa: BLE001 - the query failed; the browser keeps what it has
            return
        sub.last = max(sub.last, sub.bus.position(sub.name))   # older patches are covered by the snapshot
        await self._out({"t": "snap", "s": sid, "d": snap})

    async def _flush(self):
        for sub in list(self.subs.values()):
            if not sub.dirty or self.closed:
                continue
            sub.dirty = False
            if sub.local:
                entries = sub.bus.since(sub.name, sub.last, limit=100)
            else:
                entries = await asyncio.to_thread(sub.bus.since, sub.name, sub.last, 100)
            if not entries:
                continue
            if sub.member:
                await self._room(sub, entries)
                continue
            if sub.live_key is not None and len(entries) > BEHIND:
                sub.last = entries[-1][0]
                await self._out({"t": "r", "s": sub.sid})      # cheaper to send the result once
                continue
            for seq, data in entries:
                sub.last = seq
                await self._out({"t": "m", "s": sub.sid, "i": seq, "d": data})
            if len(entries) == 100:
                sub.dirty = True                              # more waiting

    async def _room(self, sub, entries):
        """Presence events: keep the room's members, pass on casts, greet newcomers."""
        greet = False
        for seq, event in entries:
            sub.last = seq
            if not isinstance(event, dict):
                continue
            if event.get("op") == "cast":
                if event.get("id") != sub.member:
                    await self._out({"t": "m", "s": sub.sid, "i": seq, "d": {"cast": event.get("d"),
                                                                           "from": event.get("id")}})
                continue
            presence.ROOMS.apply(sub.name, event)
            if event.get("op") == "join" and event.get("id") != sub.member:
                greet = True                             # tell the newcomer we're here
        if greet:
            self._announce(sub, {"op": "beat", "id": sub.member})
        if len(entries) == 100:
            sub.dirty = True
        await self._show_members()

    # -------------------------------------------------------------- session
    def _session_ok(self):
        if self.payload is None:
            return True
        from pyweb import auth
        return auth.session_valid(self.payload)

    def _drop(self, sub):
        if sub.member:
            presence.ROOMS.apply(sub.name, {"op": "leave", "id": sub.member})
            self._announce(sub, {"op": "leave", "id": sub.member})
            sub.member = None
        if sub.unsub is not None:
            sub.unsub()
        if sub.live_key is not None:
            from pyweb import livedata
            livedata.REGISTRY.watching(sub.live_key, -1)

    def _release(self):
        for sub in list(self.subs.values()):
            self._drop(sub)
        self.subs.clear()
        release = getattr(self, "on_release", None)
        if release is not None:
            self.on_release = None
            release()


class _TooSlow(Exception):
    pass


def open_session(server, req, headers):
    """A :class:`LiveSession` for an upgrade request, or ``(status, message)`` to refuse it.

    ``headers`` maps lower-case names to values.
    """
    from pyweb.config import settings
    from pyweb.runtime.server import STREAM_SLOTS, _max_streams, client_ip
    if not allowed_origin(headers, trust_proxy=bool(settings().trust_proxy)):
        return 403, "cross-site WebSocket refused (set PYWEB_ALLOWED_ORIGINS to allow another origin)"
    key = client_ip(req)
    if not STREAM_SLOTS.take(key, _max_streams()):
        return 429, "too many open live connections from this address"

    def session():
        from pyweb.context import session as s
        return s.user()

    try:
        payload = server.in_request(req, session)
    except Exception:  # noqa: BLE001
        STREAM_SLOTS.give_back(key)
        raise
    live = LiveSession(server, req, payload)
    live.on_release = lambda: STREAM_SLOTS.give_back(key)
    OPEN.add(live)
    return live


#: Open sockets, so shutdown can close them (code 1001: the browser reconnects elsewhere).
OPEN: "weakref.WeakSet[LiveSession]" = weakref.WeakSet()


def close_all():
    """Ask every open socket to close (on shutdown). Returns how many."""
    sessions = list(OPEN)
    for live in sessions:
        live.going_away()
    return len(sessions)
