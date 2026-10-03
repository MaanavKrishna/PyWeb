"""The playground's Python side, run inside Pyodide.

It is a tiny PyWeb host: ``load()`` compiles and starts the app the same
way ``pyweb dev`` does (pyweb.testing.TestClient wraps the same Site), and
``request()`` answers HTTP requests from the preview: pages, static files
and @server calls. Results go back to JavaScript as JSON strings.
"""

import json
import os
import sys
import time
import traceback

# Pyodide runs on the page's main thread: a sleeping server function (like the
# AI chat's demo model) would freeze the page, and streams arrive in one piece
# here anyway.
time.sleep = lambda _seconds: None

sys.path.insert(0, "/home/pyodide/lib")

from pyweb.mcp import _error_record  # noqa: E402  (line numbers + fix hints, like the MCP server)
from pyweb.testing import TestClient  # noqa: E402

APP_DIR = "/home/pyodide/app"
APP = APP_DIR + "/app.pyweb"
_client = None


def _forget_app_modules():
    for name, mod in list(sys.modules.items()):
        if (getattr(mod, "__file__", "") or "").startswith(APP_DIR):
            del sys.modules[name]


def _app_line(exc):
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        if frame.filename == APP:
            return frame.lineno
    return None


def install_static(name, text):
    """Put a file in the app's static/ folder (the examples' stylesheets)."""
    os.makedirs(APP_DIR + "/static", exist_ok=True)
    with open(APP_DIR + "/static/" + name, "w") as fh:
        fh.write(text)


def load(source):
    """Compile and start ``source``; JSON with pages or the first error."""
    global _client
    os.makedirs(APP_DIR, exist_ok=True)
    with open(APP, "w") as fh:
        fh.write(source)
    _forget_app_modules()
    try:
        client = TestClient(APP)
    except (SyntaxError, ValueError) as exc:  # compile errors
        return json.dumps({"ok": False, "error": _error_record(exc, "app.pyweb")})
    except Exception as exc:  # noqa: BLE001 - the app's own module code failed
        return json.dumps({"ok": False, "error": {"line": _app_line(exc),
                                                  "message": f"{type(exc).__name__}: {exc}"}})
    if _client is not None:
        client.cookies = dict(_client.cookies)  # stay logged in across edits
    _client = client
    pages = []
    for name, page in client.site.app.compiled["pages"].items():
        if page.get("route") is None:  # error pages
            continue
        pages.append({"name": name, "route": page["route"], "js": page["js"],
                      "placement": {k: list(v) for k, v in page["placement"].items() if not k.startswith("__")}})
    return json.dumps({"ok": True, "pages": pages})


def request(method, path, body="", content_type=""):
    """One HTTP request to the running app; JSON with status, headers, body."""
    headers = {"Content-Type": content_type} if content_type else None
    r = _client.request(method, path, body.encode() if body else b"", headers)
    hdrs = {}
    for k, v in r.headers:
        hdrs[k.lower()] = v
    return json.dumps({"status": r.status, "headers": hdrs, "body": r.text})
