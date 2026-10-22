"""Content-Security-Policy violation reports.

PyWeb's policy asks browsers to report what it blocked to ``/__pyweb/csp-report``
(``report-uri``; reports in the Reporting API's format are read too, for a
``PYWEB_CSP`` that uses ``report-to``). A report usually means one of three things: an extension injecting
script (noise), a page that needs a source added to ``PYWEB_CSP``, or
someone trying to inject script into your pages. Each report becomes a log
line (``pyweb.csp``, at most a few a second) and a count in the
``pyweb_csp_violations_total`` metric, by directive.

Reports come from anyone's browser, so they're treated as untrusted input:
the body is capped, only a few fields are kept, query strings and fragments
are dropped from URLs (they can carry tokens), and everything is shortened.
"""

from __future__ import annotations

import json
import logging
import threading
import time

PATH = "/__pyweb/csp-report"
MAX_BODY = 16 * 1024
LOG_PER_MINUTE = 120

log = logging.getLogger("pyweb.csp")

_DIRECTIVES = {
    "default-src", "script-src", "script-src-elem", "script-src-attr", "style-src", "style-src-elem",
    "style-src-attr", "img-src", "font-src", "connect-src", "media-src", "object-src", "frame-src",
    "child-src", "worker-src", "manifest-src", "form-action", "frame-ancestors", "base-uri",
    "require-trusted-types-for", "trusted-types",
}
_lock = threading.Lock()
_window = [0.0, 0]      # minute start, lines logged in it


def _metric():
    from .telemetry.instruments import csp_violations
    return csp_violations


def _clean_url(value):
    """A URL without its query string or fragment, shortened; keywords ("inline", "eval") as they are."""
    text = str(value or "")[:500]
    for sep in ("?", "#"):
        text = text.split(sep, 1)[0]
    return text[:200]


def _directive(value):
    name = str(value or "").strip().split(" ", 1)[0].lower()
    return name if name in _DIRECTIVES else "other"


def parse(content_type, body):
    """Normalised reports from a ``report-uri`` or Reporting API body; [] if it isn't one."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return []
    items = []
    if isinstance(data, dict) and isinstance(data.get("csp-report"), dict):          # report-uri
        r = data["csp-report"]
        items.append({"directive": r.get("effective-directive") or r.get("violated-directive"),
                      "blocked": r.get("blocked-uri"), "page": r.get("document-uri"),
                      "source": r.get("source-file"), "line": r.get("line-number"),
                      "sample": r.get("script-sample"), "disposition": r.get("disposition")})
    elif isinstance(data, list):                                                      # report-to
        for entry in data[:20]:
            if not isinstance(entry, dict) or entry.get("type") != "csp-violation":
                continue
            r = entry.get("body") if isinstance(entry.get("body"), dict) else {}
            items.append({"directive": r.get("effectiveDirective"), "blocked": r.get("blockedURL"),
                          "page": r.get("documentURL") or entry.get("url"), "source": r.get("sourceFile"),
                          "line": r.get("lineNumber"), "sample": r.get("sample"),
                          "disposition": r.get("disposition")})
    out = []
    for r in items:
        line = r["line"]
        out.append({"directive": _directive(r["directive"]), "blocked": _clean_url(r["blocked"]),
                    "page": _clean_url(r["page"]), "source": _clean_url(r["source"]),
                    "line": line if isinstance(line, int) and not isinstance(line, bool) else None,
                    "sample": str(r["sample"] or "")[:80],
                    "disposition": "report" if r["disposition"] == "report" else "enforce"})
    return out


def _may_log():
    now = time.monotonic()
    with _lock:
        if now - _window[0] >= 60:
            _window[0], _window[1] = now, 0
        _window[1] += 1
        return _window[1] <= LOG_PER_MINUTE


def handle(method, headers, body):
    """The response to a report: ``(status, headers, body)``."""
    if method != "POST":
        return 405, [("Allow", "POST"), ("Content-Type", "text/plain")], b"method not allowed"
    if len(body or b"") > MAX_BODY:
        return 413, [("Content-Type", "text/plain")], b"report too large"
    ctype = next((v for k, v in (headers or {}).items() if k.lower() == "content-type"), "")
    if not ctype.split(";")[0].strip().lower() in ("application/csp-report", "application/reports+json",
                                                   "application/json"):
        return 415, [("Content-Type", "text/plain")], b"not a report"
    for r in parse(ctype, body or b""):
        _metric().inc(directive=r["directive"])
        if _may_log():
            where = f" at {r['source']}:{r['line']}" if r["source"] else ""
            log.warning("CSP blocked %s on %s (%s)%s%s", r["blocked"] or "something", r["page"] or "a page",
                        r["directive"], where, f": {r['sample']!r}" if r["sample"] else "",
                        extra={"csp": r})
    return 204, [("Cache-Control", "no-store")], b""
