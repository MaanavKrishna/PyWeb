"""Presence: who is looking at something right now, plus ephemeral broadcasts.

In a page::

    from pyweb import presence, join

    @app.page("/docs/{doc_id}")
    def Doc(doc_id: int):
        room = presence(f"doc:{doc_id}")        # on the server, while rendering
        people = []

        def on_mount():
            here = join(room, {"name": my_name}, on_members=lambda m: people = m)
            ...  here.cast({"x": 10, "y": 20})  # e.g. a cursor, to everyone else in the room

Members are the ``info`` each page joined with (at most 1 KB of JSON),
plus ``"user"`` (the signed-in user id, set by the server so it can't be
faked) and ``"id"`` (one per open page). A member leaves when its page
closes, or ``TTL`` seconds after its server stopped renewing it (a crashed
process). With ``PYWEB_REDIS_URL`` rooms span every server.

Presence needs the page's WebSocket; where sockets are unavailable,
``join`` reports no members and casts go nowhere.
"""

from __future__ import annotations

import threading
import time

PREFIX = "pyweb.presence:"
TTL = 45.0               # a member not renewed for this long is gone
BEAT = 15.0              # how often a server renews its members
MAX_INFO = 1024
MAX_CAST = 4096


class Rooms:
    """Members per room, kept in step from the bus events every process sees."""

    def __init__(self):
        self.lock = threading.Lock()
        self.rooms: dict[str, dict] = {}        # room -> {member id: [info, expires, order]}
        self.order = 0

    def apply(self, room, event, now=None):
        now = time.monotonic() if now is None else now
        op, mid = event.get("op"), event.get("id")
        with self.lock:
            members = self.rooms.setdefault(room, {})
            if op in ("join", "beat") and mid:
                if mid in members:
                    members[mid][1] = now + TTL
                    if op == "join":
                        members[mid][0] = event.get("info") or {}
                else:
                    self.order += 1
                    members[mid] = [event.get("info") or {}, now + TTL, self.order]
            elif op == "leave" and mid:
                members.pop(mid, None)

    def members(self, room, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            members = self.rooms.get(room, {})
            for mid in [m for m, (_i, exp, _o) in members.items() if exp < now]:
                del members[mid]
            if not members:
                self.rooms.pop(room, None)
            return [dict(info, id=mid) for mid, (info, _e, _o) in sorted(members.items(), key=lambda kv: kv[1][2])]


ROOMS = Rooms()


def presence(name: str) -> str:
    """A token for presence room ``name``: call while rendering, pass to ``join()`` in the browser."""
    from .context import sign_key
    from .realtime import current_bus, current_sid, make_feed
    chan = PREFIX + str(name)
    return make_feed(chan, sign_key("feed"), since=current_bus().position(chan), sid=current_sid())


def join(room, info=None, on_members=None, on_cast=None):  # noqa: ARG001 - the browser implementation takes these
    """Browser-only: join ``room`` (from :func:`presence`); returns a handle with ``cast(data)`` and ``leave()``."""
    raise RuntimeError("join() runs in the browser: call it from on_mount() or an event handler")


def clean_info(info, user=None):
    """The member info a page may publish: a small JSON object, with the server's ``user``."""
    import json
    if not isinstance(info, dict):
        info = {}
    info = {str(k): v for k, v in info.items() if k not in ("user", "id")}
    if len(json.dumps(info, default=str)) > MAX_INFO:
        raise ValueError(f"presence info must be under {MAX_INFO} bytes of JSON")
    info["user"] = user
    return info
