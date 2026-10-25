"""``pyweb dev --share``: show the app you're building to someone else, through a temporary public URL.

A tunnel (``cloudflared`` when it's installed, else ``ssh`` to localhost.run) forwards a public
HTTPS address to the dev server. Nobody gets in with the address alone: the printed link carries a
token (``/__pyweb/share?t=...``) that sets a cookie, and every request through the tunnel needs that
cookie, or it gets 403. The link works until ``pyweb dev`` stops; anyone you give it to can use it.

People coming in through the link use the app itself and see your saves live (hot reload). They
don't get the developer's tools: no toolbar (it shows SQL and request data), no inspector, no
"open in editor", no AI fixes or edits, no theme saving, no agent endpoints. Those answer requests
for a loopback address only, and a tunnel's requests name the public host.

``PYWEB_SHARE_COMMAND`` replaces the tunnel command (``{port}`` is filled in); the first ``https://``
address it prints is the public URL.
"""

from __future__ import annotations

import hmac
import os
import re
import secrets
import shlex
import shutil
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlparse

ENTRY = "/__pyweb/share"
COOKIE = "pyweb_share"
_URL = re.compile(r"https://[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SKIP = ("https://www.cloudflare.com", "https://developers.cloudflare.com", "https://github.com", "https://admin.localhost.run",
         "https://localhost.run/docs", "https://twitter.com")

_REFUSED = ("<!doctype html><html lang=en><meta charset=utf-8><title>Ask for the link</title>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<style>body{font:16px/1.5 system-ui,sans-serif;margin:3rem auto;max-width:34rem;padding:0 1rem;color:#334155}"
            "</style><h1>Ask for the link</h1><p>This is someone's app in development. Open the full link they "
            "sent you (it ends in <code>?t=...</code>) to see it.</p></html>")


def _lower(headers):
    return {str(k).lower(): v for k, v in (headers or {}).items()}


def commands(port):
    """The tunnel commands to try, in order."""
    custom = os.environ.get("PYWEB_SHARE_COMMAND")
    if custom:
        return [[p.replace("{port}", str(port)) for p in shlex.split(custom)]]
    out = []
    if shutil.which("cloudflared"):
        out.append(["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"])
    if shutil.which("ssh"):
        out.append(["ssh", "-T", "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30",
                    "-o", "ExitOnForwardFailure=yes", "-R", f"80:127.0.0.1:{port}", "nokey@localhost.run"])
    return out


HOW_TO = ("couldn't start a tunnel. Install cloudflared (https://developers.cloudflare.com/cloudflare-one/"
          "connections/connect-networks/downloads/, e.g. `brew install cloudflared`), or make sure `ssh` can "
          "reach localhost.run, then run pyweb dev --share again.")


class Share:
    """The gate in front of the dev server for requests through the tunnel."""

    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.session = secrets.token_urlsafe(32)
        self.url = None             # https://name.trycloudflare.com
        self.host = None            # name.trycloudflare.com
        self.proc = None
        self.error = None

    # ---------------------------------------------------------------- the tunnel
    def start(self, port, timeout=30.0):
        """Start a tunnel to ``port``; returns the public URL or None (``self.error`` says why)."""
        for cmd in commands(port):
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, bufsize=1)
            except OSError:
                continue
            found = []
            done = threading.Event()

            def read(proc=proc):
                for line in proc.stdout:
                    if not found:
                        for m in _URL.finditer(line):
                            url = m.group(0).rstrip(".")
                            if not url.startswith(_SKIP):
                                found.append(url)
                                done.set()
                                break
                done.set()                   # the tunnel exited
            threading.Thread(target=read, daemon=True, name="pyweb-share").start()
            done.wait(timeout)
            if found:
                self.proc = proc
                self.url = found[0]
                self.host = urlparse(self.url).hostname
                import atexit
                atexit.register(self.stop)
                return self.url
            proc.kill()
        self.error = HOW_TO
        return None

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def link(self):
        return f"{self.url}{ENTRY}?t={self.token}" if self.url else None

    # ---------------------------------------------------------------- the gate
    def guest(self, headers):
        """Did this request come through the tunnel (its Host is the public name)?"""
        if not self.host:
            return False
        host = _lower(headers).get("host", "").rsplit(":", 1)[0].lower()
        return host == self.host

    def check(self, method, path, headers):
        """None to let a request through, else the response (the entry link sets the cookie)."""
        if not self.guest(headers):
            return None
        h = _lower(headers)
        if urlparse(path).path == ENTRY:
            token = (parse_qs(urlparse(path).query).get("t") or [""])[0]
            if not hmac.compare_digest(token, self.token):
                return _answer(403, _REFUSED)
            cookie = f"{COOKIE}={self.session}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=86400"
            return 303, [("Location", "/"), ("Set-Cookie", cookie), ("Cache-Control", "no-store"),
                         ("Referrer-Policy", "no-referrer"), ("Content-Length", "0")], b""
        from pyweb.serve import parse_cookies
        sent = parse_cookies(h.get("cookie", "")).get(COOKIE, "")
        if not sent or not hmac.compare_digest(sent, self.session):
            return _answer(403, _REFUSED)
        return None


def _answer(status, html):
    return status, [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store"),
                    ("X-Content-Type-Options", "nosniff")], html.encode()


def wait_and_print(share, port, out, qr=True):
    """Start the tunnel (in the background) and print the link and a QR code when it's up."""
    def run():
        started = time.time()
        url = share.start(port)
        if url is None:
            print(f"share: {share.error}", file=out, flush=True)
            return
        link = share.link()
        print(f"Share (anyone with this link can use the app; dev tools stay on this machine):\n  {link}", file=out)
        if qr and getattr(out, "isatty", lambda: False)():
            from pyweb import qr as Q
            try:
                print(Q.terminal(link), file=out)
            except ValueError:
                pass
        print(f"  (tunnel up in {time.time() - started:.1f}s)", file=out, flush=True)
    threading.Thread(target=run, daemon=True, name="pyweb-share-start").start()
