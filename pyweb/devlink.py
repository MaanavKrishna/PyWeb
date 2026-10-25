"""The link between ``pyweb dev`` and ``pyweb mcp``: assistants see the running dev server.

When ``pyweb dev`` starts, it writes ``.pyweb/dev.json`` next to the app (its URL and a random
token, readable only by you) and removes it on exit. The MCP tools find it and ask the dev server
for its compile error, log lines, requests, jobs and emails at ``/__pyweb/dev/agent/...``. Those
endpoints answer only with the token (``X-PyWeb-Dev-Token``) and only on a loopback address, so a
web page or another person on the machine can't read them.
"""

from __future__ import annotations

import atexit
import collections
import hmac
import json
import logging
import os
import secrets
import threading
import time
import traceback

PREFIX = "/__pyweb/dev/agent/"
HEADER = "X-PyWeb-Dev-Token"
LOG_LINES = 1000


def info_path(app_path):
    return os.path.join(os.path.dirname(os.path.abspath(app_path)), ".pyweb", "dev.json")


# ------------------------------------------------------------------ the dev server's side

class LogBuffer(logging.Handler):
    """The last log records, numbered, for ``/__pyweb/dev/agent/logs``."""

    def __init__(self, size=LOG_LINES):
        super().__init__(logging.DEBUG)
        self.records = collections.deque(maxlen=size)
        self.seq = 0
        self._guard = threading.Lock()

    def emit(self, record):
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string still gets logged
            text = str(record.msg)
        entry = {"time": round(record.created, 3), "level": record.levelname.lower(), "logger": record.name,
                 "message": text[:4000]}
        if record.exc_info:
            entry["traceback"] = "".join(traceback.format_exception(*record.exc_info))[-6000:]
        with self._guard:
            self.seq += 1
            entry["seq"] = self.seq
            self.records.append(entry)


class Link:
    """Created by ``pyweb dev``: publishes ``.pyweb/dev.json`` and answers the agent endpoints."""

    def __init__(self, state):
        self.state = state
        self.token = secrets.token_urlsafe(32)
        self.started = time.time()
        self.logs = LogBuffer()
        logging.getLogger().addHandler(self.logs)
        if logging.getLogger().level > logging.INFO:
            logging.getLogger().setLevel(logging.INFO)
        self.path = None

    def publish(self, url):
        self.path = info_path(self.state.path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        ignore = os.path.join(os.path.dirname(self.path), ".gitignore")
        if not os.path.exists(ignore):
            with open(ignore, "w", encoding="utf-8") as fh:
                fh.write("*\n")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"url": url, "pid": os.getpid(), "token": self.token, "app": os.path.abspath(self.state.path),
                       "started": self.started}, fh)
        atexit.register(self.unpublish)

    def unpublish(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                mine = json.load(fh).get("pid") == os.getpid()
            if mine:
                os.remove(self.path)
        except (OSError, ValueError, TypeError):
            pass

    def handle(self, method, path, headers, client=None, body=b""):
        """Answer ``/__pyweb/dev/agent/...``, or None for other paths."""
        if not path.startswith(PREFIX):
            return None
        sent = next((v for k, v in (headers or {}).items() if k.lower() == HEADER.lower()), "")
        host = client[0] if isinstance(client, (tuple, list)) else (client or "127.0.0.1")
        if not sent or not hmac.compare_digest(sent, self.token) or host not in ("127.0.0.1", "::1", "localhost"):
            return _json({"error": "this endpoint is for `pyweb mcp` on this machine"}, 403)
        what, _, query = path[len(PREFIX):].partition("?")
        params = dict(p.partition("=")[::2] for p in query.split("&") if p)
        if what == "status":
            return _json(self.status())
        if what == "assist" and method == "GET":         # pyweb mcp, for a client that offers sampling
            from . import assist
            # short polls: a waiting request holds a server thread, and Ctrl+C waits for it
            return _json({"job": assist.SAMPLING.next(min(float(params.get("wait") or 5), 5.0))})
        if what.startswith("assist/") and method == "POST":
            from . import assist
            try:
                data = json.loads(body or b"{}")
            except ValueError:
                data = {"error": "bad answer"}
            return _json({"ok": assist.SAMPLING.answer(what[len("assist/"):], data)})
        if what == "requests-pending":
            from . import assist
            return _json({"requests": list(assist.PENDING)})
        if what == "logs":
            since = int(params.get("since") or 0)
            level = params.get("level") or "debug"
            order = ["debug", "info", "warning", "error", "critical"]
            floor = order.index(level) if level in order else 0
            items = [r for r in list(self.logs.records) if r["seq"] > since
                     and (order.index(r["level"]) if r["level"] in order else 0) >= floor]
            return _json({"logs": items[-int(params.get("limit") or 200):], "last": self.logs.seq})
        from .telemetry import devtools
        if what in ("requests", "jobs", "mail"):
            loaded = getattr(self.state.site, "app", None)
            q = f"?{query}" if query else ""
            return devtools.handle("GET", devtools.PREFIX + what + q, headers, app=getattr(loaded, "app", None))
        return _json({"error": f"unknown: {what}"}, 404)

    def status(self):
        st = self.state
        err = None
        if st.error is not None:
            err = {"message": f"{type(st.error).__name__}: {st.error}",
                   "line": getattr(st.error, "lineno", None), "file": getattr(st.error, "filename", None),
                   "traceback": "".join(traceback.format_exception(type(st.error), st.error,
                                                                   st.error.__traceback__))[-6000:]}
        pages = []
        if st.site is not None and err is None:
            compiled = getattr(st.site.app, "compiled", None) or {}
            pages = [{"page": n, "route": p.get("route")} for n, p in compiled.get("pages", {}).items()]
        from .telemetry import RECENT
        failures = [{"id": r.get("id"), "method": r.get("method"), "path": r.get("path"), "status": r.get("status"),
                     "error": r.get("error")} for r in list(RECENT) if (r.get("status") or 0) >= 500][-10:]
        return {"app": os.path.abspath(st.path), "pid": os.getpid(), "reloads": st.version,
                "uptime_s": round(time.time() - self.started), "compiles": err is None, "error": err,
                "pages": pages, "recent_failures": failures, "log_lines": self.logs.seq}


def _json(data, status=200):
    return status, [("Content-Type", "application/json"), ("Cache-Control", "no-store")], \
        json.dumps(data, default=str).encode()


# ------------------------------------------------------------------ the MCP side

def find(app_path="app.pyweb"):
    """The running ``pyweb dev`` for this app (``{url, token, pid, app}``), or None."""
    try:
        with open(info_path(app_path), encoding="utf-8") as fh:
            info = json.load(fh)
    except (OSError, ValueError):
        return None
    if os.path.abspath(info.get("app", "")) != os.path.abspath(app_path):
        return None
    try:
        os.kill(int(info["pid"]), 0)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        get(info, "status", timeout=2)
    except Exception:  # noqa: BLE001 - stale file, or a server that's still starting
        return None
    return info


def get(info, what, timeout=10, data=None):
    import urllib.request
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(info["url"].rstrip("/") + PREFIX + what, data=body,
                                 headers={HEADER: info["token"], "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # it's on loopback: no proxy
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _need(args):
    path = args.get("path") or "app.pyweb"
    info = find(path)
    if info is None:
        return path, None
    return path, info


def tool_dev(args):
    """Is `pyweb dev` running for this app? Its URL, whether it compiles (with the error), recent failures."""
    path, info = _need(args)
    if info is None:
        return {"running": False,
                "hint": f"no pyweb dev for {path}; start one with `pyweb dev {path}` to see its live logs and "
                        "errors here (the other tools work without it)"}
    status = get(info, "status")
    return {"running": True, "url": info["url"], **status}


def tool_logs(args):
    """Log lines from the running `pyweb dev` (errors with tracebacks)."""
    path, info = _need(args)
    if info is None:
        return {"running": False, "logs": [], "hint": f"start `pyweb dev {path}` first"}
    q = f"logs?since={int(args.get('since') or 0)}&level={args.get('level') or 'info'}&limit={int(args.get('limit') or 100)}"
    data = get(info, q)
    return {"running": True, **data, "hint": "pass since=<last> next time for only the new lines"}


def dev_requests(path, limit):
    info = find(path)
    if info is None:
        return None
    return get(info, "requests")["requests"][-limit:]


def dev_jobs(path):
    info = find(path)
    return None if info is None else get(info, "jobs")


def tool_assist_requests(args):
    """What the developer asked for from the page ("Fix with AI" / "Edit with AI") that no AI answered."""
    path, info = _need(args)
    if info is None:
        return {"running": False, "requests": [], "hint": f"start `pyweb dev {path}` first"}
    reqs = get(info, "requests-pending")["requests"]
    return {"running": True, "requests": reqs,
            "hint": "Do each one by editing the file named (pyweb_check after); the page updates by itself." if reqs
            else "Nothing was asked from the page."}


_S, _I, _B, _A, _O = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}, {"type": "array"}, {"type": "object"}
_PATH = {"type": "string", "description": "Path to the .pyweb file (default: app.pyweb)."}

TOOLS = [
    {"name": "pyweb_dev", "fn": tool_dev, "readOnly": True, "title": "The running dev server",
     "description": "Whether `pyweb dev` is running for this app and, if so, its URL, whether the app compiles "
                    "right now (with the error and traceback), its pages, recent 500 errors and how often it "
                    "reloaded. The dev server writes .pyweb/dev.json so this finds it.",
     "inputSchema": {"type": "object", "properties": {"path": _PATH}},
     "output": {"type": "object", "required": ["running"], "properties": {
         "running": _B, "url": _S, "compiles": _B, "error": {}, "pages": _A, "recent_failures": _A, "hint": _S}}},
    {"name": "pyweb_assist_requests", "fn": tool_assist_requests, "readOnly": True, "title": "Requests from the page",
     "description": "Fixes and changes the developer asked for in the browser (Fix with AI on an error page, "
                    "Alt+Shift-click → Edit with AI) while no AI was set up to answer them: each has the file, the "
                    "error or the element's line, and the instruction. Do them by editing the file.",
     "inputSchema": {"type": "object", "properties": {"path": _PATH}},
     "output": {"type": "object", "required": ["running", "requests"], "properties": {
         "running": _B, "requests": _A, "hint": _S}}},
    {"name": "pyweb_logs", "fn": tool_logs, "readOnly": True, "title": "Dev server logs",
     "description": "Log lines from the running `pyweb dev` (what the terminal shows: warnings, errors with "
                    "tracebacks, slow queries, job failures). since=<last> returns only newer lines.",
     "inputSchema": {"type": "object", "properties": {
         "path": _PATH, "since": {"type": "integer"},
         "level": {"type": "string", "enum": ["debug", "info", "warning", "error", "critical"]},
         "limit": {"type": "integer"}}},
     "output": {"type": "object", "required": ["running"], "properties": {
         "running": _B, "logs": _A, "last": _I, "hint": _S}}},
]
