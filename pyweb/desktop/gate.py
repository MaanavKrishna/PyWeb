"""The desktop app's front door: only its own window gets in.

The app's server listens on ``127.0.0.1`` on a random port. Other programs on the
computer (and other people's accounts on it) can reach that port, and so can web pages
in the user's browser. So every request and WebSocket must have:

* the window's cookie: the window first opens ``/__pyweb/desktop/start?t=<one-time
  token>``, which sets an HttpOnly, SameSite=Strict cookie only this window holds;
* ``Host: 127.0.0.1:<port>`` exactly (a website using DNS rebinding sends its own name);
* no ``Origin`` other than the app's own, and no ``Sec-Fetch-Site: cross-site``.

Anything else gets 403. The gate never sends CORS headers.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
from urllib.parse import parse_qs, urlparse

COOKIE = "pyweb_desktop"
START = "/__pyweb/desktop/start"
OPEN = "/__pyweb/desktop/open"
SCRIPT = "/__pyweb/desktop.js"
READY = "/__pyweb/desktop/ready"
BROWSER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runtime", "browser")

_REFUSED = ("<!doctype html><html lang=en><meta charset=utf-8><title>Not here</title>"
            "<style>body{font:16px/1.5 system-ui,sans-serif;margin:3rem;color:#334155}</style>"
            "<h1>Open this app from its window</h1>"
            "<p>This address belongs to a desktop app running on this computer. "
            "Only the app's own window can use it.</p></html>")


def _lower(headers):
    return {str(k).lower(): v for k, v in (headers or {}).items()}


def _cookie(header, name):
    from pyweb.net.http import parse_cookies
    return parse_cookies(header or "").get(name, "")


def _answer(status, body, ctype="text/plain; charset=utf-8", extra=()):
    raw = body.encode() if isinstance(body, str) else body
    return status, [("Content-Type", ctype), ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                    *extra], raw


class DesktopSite:
    """Wraps a :class:`pyweb.hosting.Site` (or the dev server's site) so only the window gets in."""

    def __init__(self, site, *, start_url="/"):
        self.site = site
        self.start_url = start_url
        self.port = None
        self._launch = secrets.token_urlsafe(32)
        self._session = secrets.token_urlsafe(32)
        self._used = False
        self._lock = threading.Lock()
        self.ready = threading.Event()       # set when the window reports a page (``pages`` lists them)
        self.pages = []

    # -- what the transport needs ------------------------------------------------------
    def __getattr__(self, name):          # max_body, time_limit, timed_out, use_transport_timeouts...
        return getattr(self.site, name)

    def bind(self, port):
        """The port the server got: requests must name it in ``Host``."""
        self.port = int(port)

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.port}"

    def launch_url(self):
        """The URL the window opens first (once)."""
        return f"{self.origin}{START}?t={self._launch}"

    # -- the checks ----------------------------------------------------------------------
    def refusal(self, headers, *, need_cookie=True):
        """Why this request may not come in (``(status, message)``), or None."""
        h = _lower(headers)
        if self.port is None or h.get("host", "") != f"127.0.0.1:{self.port}":
            return 403, "wrong host"
        origin = h.get("origin")
        if origin is not None and origin != self.origin:
            return 403, "wrong origin"
        if h.get("sec-fetch-site") == "cross-site":
            return 403, "cross-site request"
        if need_cookie and not hmac.compare_digest(_cookie(h.get("cookie"), COOKIE), self._session):
            return 403, "not the app's window"
        return None

    def _start(self, path):
        token = (parse_qs(urlparse(path).query).get("t") or [""])[0]
        with self._lock:
            ok = not self._used and hmac.compare_digest(token, self._launch)
            if ok:
                self._used = True             # one window, one launch
        if not ok:
            return _answer(403, _REFUSED, "text/html; charset=utf-8")
        cookie = f"{COOKIE}={self._session}; Path=/; HttpOnly; SameSite=Strict"
        return _answer(303, "", extra=(("Location", self.start_url), ("Set-Cookie", cookie)))

    def _open(self, method, headers, body):
        if method != "POST":
            return _answer(405, "POST only", extra=(("Allow", "POST"),))
        if "json" not in _lower(headers).get("content-type", ""):
            return _answer(415, "send JSON")
        try:
            url = json.loads(body or b"{}").get("url")
        except (ValueError, AttributeError):
            return _answer(400, "bad JSON")
        from pyweb import desktop
        try:
            desktop.open_url(url)
        except (ValueError, desktop.DesktopUnavailable) as exc:
            return _answer(422, str(exc))
        return _answer(204, b"")

    def _ready(self, method, body):
        if method != "POST":
            return _answer(405, "POST only", extra=(("Allow", "POST"),))
        try:
            info = json.loads(body or b"{}")
            if not isinstance(info, dict):
                raise ValueError
        except ValueError:
            return _answer(400, "bad JSON")
        with self._lock:
            self.pages = (self.pages + [{"title": str(info.get("title", ""))[:200], "path": str(info.get("path", ""))[:500],
                                         "root": bool(info.get("root"))}])[-20:]
        self.ready.set()
        return _answer(204, b"")

    # -- requests --------------------------------------------------------------------------
    def respond(self, method, path, headers, body=b"", client=None):
        bare = path.split("?")[0]
        if bare == START:
            refused = self.refusal(headers, need_cookie=False)
            return _answer(refused[0], _REFUSED, "text/html; charset=utf-8") if refused else self._start(path)
        refused = self.refusal(headers)
        if refused is not None:
            return _answer(refused[0], _REFUSED, "text/html; charset=utf-8")
        if bare == SCRIPT:
            with open(os.path.join(BROWSER_DIR, "desktop.js"), "rb") as fh:
                return _answer(200, fh.read(), "text/javascript; charset=utf-8")
        if bare == OPEN:
            return self._open(method, headers, body)
        if bare == READY:
            return self._ready(method, body)
        status, hdrs, raw = self.site.respond(method, path, headers, body, client=client)
        hdrs = [(k, v) for k, v in hdrs if not k.lower().startswith("access-control-")]
        return status, hdrs, raw

    def websocket(self, path, headers, client=None):
        refused = self.refusal(headers)
        if refused is not None:
            return refused
        return self.site.websocket(path, headers, client)
