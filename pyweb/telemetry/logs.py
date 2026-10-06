"""Logs: one JSON object per line in production, readable lines while developing.

Every record made while handling a request or running a job carries its
request id, trace id, route and user automatically (from the request's
telemetry context), so ``grep request_id=...`` finds everything one click
caused, across the web process and the worker that ran its job.

Secrets never reach the log: values of fields named like ``password``,
``token``, ``secret``, ``authorization``, ``cookie``, ``api_key`` (plus
``PYWEB_LOG_REDACT=name,...``) are replaced, and so are ``name=value`` pairs
with those names inside messages and URLs.

``PYWEB_LOG_FORMAT`` = ``json`` | ``text`` (default: json in production),
``PYWEB_LOG_LEVEL`` = ``info`` (default), ``debug``, ``warning``, ...
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sys

SENSITIVE = ("password", "passwd", "secret", "token", "authorization", "cookie", "api_key", "apikey",
             "private_key", "session", "csrf", "otp", "code_verifier", "client_secret", "credit_card", "ssn")
REDACTED = "[redacted]"
_STD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}


def sensitive_names():
    extra = [n.strip().lower() for n in os.environ.get("PYWEB_LOG_REDACT", "").split(",") if n.strip()]
    return SENSITIVE + tuple(extra)


def is_sensitive(name):
    low = str(name).lower()
    return any(s in low for s in sensitive_names())


def _pairs_re():
    names = "|".join(re.escape(s) for s in sensitive_names())
    # name=value or "name": "value" inside free text and query strings
    return re.compile(rf"""((?:[\w.-]*(?:{names})[\w.-]*)\s*["']?\s*[=:]\s*["']?)([^\s&"',;}}]+)""", re.I)


def redact_text(text):
    if not isinstance(text, str) or not text:
        return text
    return _pairs_re().sub(lambda m: m.group(1) + REDACTED, text)


def redact(value, name=None):
    """``value`` with secrets removed (recursively for dicts and lists)."""
    if name is not None and is_sensitive(name):
        return REDACTED
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _context():
    from .tracing import current
    req = current()
    if req is None:
        return {}
    out = {"request_id": req.request_id, "trace_id": req.trace_id}
    if req.route:
        out["route"] = req.route
    if req.user is not None:
        out["user"] = req.user
    if req.job:
        out["job"] = req.job
    return out


class JSONFormatter(logging.Formatter):
    def format(self, record):
        out = {"ts": dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(timespec="milliseconds"),
               "level": record.levelname.lower(), "logger": record.name,
               "msg": redact_text(record.getMessage())}
        out.update(_context())
        for k, v in vars(record).items():
            if k not in _STD and not k.startswith("_"):
                out[k] = redact(v, k)
        if record.exc_info:
            out["error"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(out, default=str)


class TextFormatter(logging.Formatter):
    COLORS = {"DEBUG": "\x1b[2m", "INFO": "", "WARNING": "\x1b[33m", "ERROR": "\x1b[31m", "CRITICAL": "\x1b[1;31m"}

    def __init__(self, color=None):
        super().__init__()
        self.color = sys.stderr.isatty() if color is None else color

    def format(self, record):
        ctx = _context()
        extras = {k: redact(v, k) for k, v in vars(record).items() if k not in _STD and not k.startswith("_")}
        if record.name == "pyweb.access":       # the message already says it all; keep warnings
            extras = {k: v for k, v in extras.items() if k == "warnings"}
            ctx = {}
        tail = " ".join(f"{k}={v}" for k, v in {**extras, **({"req": ctx["request_id"][:16]} if ctx else {})}.items())
        line = f"{record.levelname.lower():7} {record.name}: {redact_text(record.getMessage())}"
        if tail:
            line += f"  [{tail}]"
        if record.exc_info:
            line += "\n" + redact_text(self.formatException(record.exc_info))
        if self.color:
            line = f"{self.COLORS.get(record.levelname, '')}{line}\x1b[0m"
        return line


_configured = False


def setup(*, production=None, stream=None, force=False):
    """Send PyWeb's (and the app's) logging to stderr in the chosen format. Called by the servers."""
    global _configured
    if _configured and not force:
        return
    from pyweb.config import settings
    production = settings().production if production is None else production
    fmt = os.environ.get("PYWEB_LOG_FORMAT", "json" if production else "text").strip().lower()
    level = os.environ.get("PYWEB_LOG_LEVEL", "info").strip().upper()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JSONFormatter() if fmt == "json" else TextFormatter())
    handler._pyweb = True
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not getattr(h, "_pyweb", False)] + [handler]
    root.setLevel(getattr(logging, level, logging.INFO))
    _configured = True
    return handler
