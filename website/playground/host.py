"""The playground's Python side, run inside Pyodide.

It is a tiny PyWeb host: ``load()`` compiles and starts the app the same
way ``pyweb dev`` does (pyweb.testing.TestClient wraps the same Site), and
``request()`` answers HTTP requests from the preview: pages, static files
and @server calls. Results go back to JavaScript as JSON strings.

Beyond that it shows what the app did: the database's tables and rows,
each request's SQL (the dev toolbar's data), background jobs (run right
after the request that queued them: Pyodide has no threads), and lets the
preview sign in as a test user.
"""

import contextlib
import io
import json
import os
import sys
import time
import traceback

# Pyodide runs on the page's main thread: a sleeping server function (like the
# AI chat's demo model) would freeze the page, and streams arrive in one piece
# here anyway.
time.sleep = lambda _seconds: None
os.environ.setdefault("PYWEB_WORKER", "0")        # no threads here: jobs run after each request

sys.path.insert(0, "/home/pyodide/lib")

from pyweb.mcp import _error_record  # noqa: E402  (line numbers + fix hints, like the MCP server)
from pyweb.testing import TestClient  # noqa: E402

APP_DIR = "/home/pyodide/app"
APP = APP_DIR + "/app.pyweb"
_client = None
_files = set()


def _forget_app_modules():
    for name, mod in list(sys.modules.items()):
        if (getattr(mod, "__file__", "") or "").startswith(APP_DIR):
            del sys.modules[name]


def _rel(path):
    """``widgets.pyweb`` for a file of the app (what the editor's tabs are called)."""
    if path and str(path).startswith(APP_DIR + "/"):
        return str(path)[len(APP_DIR) + 1:]
    return "app.pyweb"


def _app_frame(exc):
    """``(file, line)`` of the innermost frame in the app's own files."""
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        if frame.filename.startswith(APP_DIR):
            return _rel(frame.filename), frame.lineno
    return "app.pyweb", None


def install_static(name, text):
    """Put a file in the app's static/ folder (the examples' stylesheets)."""
    os.makedirs(APP_DIR + "/static", exist_ok=True)
    with open(APP_DIR + "/static/" + name, "w") as fh:
        fh.write(text)


@contextlib.contextmanager
def _captured():
    """Collect what the app prints (shown in the playground's Console)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        yield out


def _write_files(files):
    """The editor's files: app.pyweb plus other .pyweb/.py modules and static/ files."""
    global _files
    for rel in _files - set(files):              # a file closed in the editor goes away here too
        with contextlib.suppress(OSError):
            os.remove(os.path.join(APP_DIR, rel))
    for rel, text in files.items():
        rel = rel.lstrip("/")
        if ".." in rel.split("/") or not rel:
            continue
        path = os.path.join(APP_DIR, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
    _files = set(files)


def _reset_database():
    """Each run starts with the tables the app's Models describe (prototype mode, like `pyweb dev`)."""
    with contextlib.suppress(OSError):
        for fn in os.listdir(APP_DIR):
            if fn.endswith((".db", ".db-journal", ".db-wal", ".db-shm", ".sqlite3")):
                os.remove(os.path.join(APP_DIR, fn))


def load(source, files_json="", keep_data=False):
    """Compile and start the app; JSON with pages or the first error.

    ``source`` is app.pyweb; ``files_json`` optionally maps other file names to their text.
    """
    global _client
    os.makedirs(APP_DIR, exist_ok=True)
    os.chdir(APP_DIR)                               # sqlite:///app.db lands next to the app
    files = json.loads(files_json) if files_json else {}
    files["app.pyweb"] = source
    _write_files(files)
    if not keep_data:
        _reset_database()
    _forget_app_modules()
    from pyweb import models as M
    from pyweb import telemetry
    M._state.db = None
    telemetry.RECENT.clear()
    with _captured() as printed:
        try:
            client = TestClient(APP)
        except (SyntaxError, ValueError) as exc:  # compile errors
            rec = _error_record(exc, "app.pyweb")
            rec["file"] = _rel(rec.get("file"))
            return json.dumps({"ok": False, "error": rec, "output": printed.getvalue()})
        except ModuleNotFoundError as exc:
            if "sqlite3" in str(exc):           # the page loads SQLite (about 1 MB) and runs the app again
                return json.dumps({"ok": False, "needs": "sqlite3", "output": printed.getvalue(),
                                   "error": {"line": None, "message": "Loading SQLite for the app's database…"}})
            return json.dumps({"ok": False, "output": printed.getvalue(),
                               "error": {"line": None, "message": f"{type(exc).__name__}: {exc}"}})
        except Exception as exc:  # noqa: BLE001 - the app's own module code failed
            file, line = _app_frame(exc)
            return json.dumps({"ok": False, "output": printed.getvalue(),
                               "error": {"file": file, "line": line, "message": f"{type(exc).__name__}: {exc}"}})
    if M.database() is not None and not _have_sqlite():
        # pyweb.db imports sqlite3 at the first query; ask the page for it now, before any request.
        return json.dumps({"ok": False, "needs": "sqlite3", "output": printed.getvalue(),
                           "error": {"line": None, "message": "Loading SQLite for the app's database…"}})
    if _client is not None:
        client.cookies = dict(_client.cookies)  # stay logged in across edits
    _client = client
    pages = []
    for name, page in client.site.app.compiled["pages"].items():
        if page.get("route") is None:  # error pages
            continue
        pages.append({"name": name, "route": page["route"], "js": page["js"],
                      "placement": {k: list(v) for k, v in page["placement"].items() if not k.startswith("__")}})
    return json.dumps({"ok": True, "pages": pages, "output": printed.getvalue(), "database": M.database() is not None,
                       "auth": _has_auth()})


def _have_sqlite():
    import importlib
    importlib.invalidate_caches()       # loadPackage just unpacked it; don't trust the cached directory listing
    try:
        import sqlite3  # noqa: F401 - Pyodide ships it as a separate package
    except ModuleNotFoundError:
        return False
    return True


def _has_auth():
    kit = getattr(getattr(_client.site.app, "app", None), "auth", None)
    return type(kit).__name__ == "AuthKit"


def _run_jobs():
    """Jobs queued by the request run now, one after another (there are no threads to run them later)."""
    from pyweb import jobs
    from pyweb.jobs import core
    if not core.REGISTRY:
        return []
    try:
        worker = jobs.Worker(schedule=False)
        before = {j["id"] for j in core.backend().list(limit=200) if j["state"] in ("done", "dead")}
        if not worker.run_inline():
            return []
        after = core.backend().list(limit=200)
        return [{"name": j["name"], "state": j["state"], "error": (j["last_error"] or "").splitlines()[0][:200]
                 if j["last_error"] else ""} for j in after if j["id"] not in before and j["state"] in ("done", "dead")]
    except Exception as exc:  # noqa: BLE001
        return [{"name": "?", "state": "error", "error": f"{type(exc).__name__}: {exc}"}]


def request(method, path, body="", content_type=""):
    """One HTTP request to the running app; JSON with status, headers, body."""
    headers = {"Content-Type": content_type} if content_type else None
    start = time.perf_counter()
    with _captured() as printed:
        r = _client.request(method, path, body.encode() if body else b"", headers)
        ran = _run_jobs() if method != "GET" else []
    hdrs = {}
    for k, v in r.headers:
        hdrs[k.lower()] = v
    return json.dumps({"status": r.status, "headers": hdrs, "body": r.text, "output": printed.getvalue(),
                       "ms": round((time.perf_counter() - start) * 1000, 1), "jobs": ran})


def recent(since=""):
    """What each request since ``since`` did: time, status, SQL, N+1 warnings, spans, jobs, emails, errors."""
    from pyweb import telemetry
    items = list(telemetry.RECENT)
    ids = [r["id"] for r in items]
    if since in ids:
        items = items[ids.index(since) + 1:]
    return json.dumps({"requests": items}, default=str)


def tables():
    """The database's tables with their columns and row counts."""
    from pyweb import models as M
    from pyweb.db import schema as S
    db = M.database()
    if db is None:
        return json.dumps({"tables": []})
    out = []
    for name, t in sorted(S.introspect(db).items()):
        count = db.execute(f"SELECT COUNT(*) FROM {db.dialect.quote(name)}").fetchone()[0]
        out.append({"name": name, "rows": count, "columns": [c.name for c in t.columns]})
    return json.dumps({"tables": out})


def rows(table, limit=100):
    """Up to ``limit`` rows of ``table`` (newest first; secrets redacted)."""
    from pyweb import models as M
    from pyweb.db import schema as S
    from pyweb.ssr import to_jsonable
    from pyweb.telemetry.logs import redact
    db = M.database()
    known = S.introspect(db)
    if table not in known:
        return json.dumps({"error": f"no table {table}"})
    cols = [c.name for c in known[table].columns]
    q = db.dialect.quote
    order = " ORDER BY " + q("id") + " DESC" if "id" in cols else ""
    res = db.execute(f"SELECT * FROM {q(table)}{order} LIMIT {int(limit)}")
    data = [[redact(v, c) for v, c in zip(to_jsonable(list(r)), res.columns)] for r in res.fetchall()]
    return json.dumps({"columns": list(res.columns), "rows": data})


def sign_in(who):
    """``""`` signs out; ``"user"`` / ``"admin"`` sign the preview in as a test account."""
    with _captured() as printed:
        if not who:
            _client.logout()
            return json.dumps({"user": None, "output": printed.getvalue()})
        roles = ["admin"] if who == "admin" else []
        email = f"{who}@example.com"
        user = _client.login(email, roles=roles, name=who.capitalize())
    label = getattr(user, "email", None) or (user or {}).get("sub") or email
    return json.dumps({"user": label, "roles": roles, "output": printed.getvalue()})
