"""``pyweb doctor``: is this project and this machine ready? One line per check, with a fix for each problem.

Each check returns ``Check(name, status, detail, fix)`` with status ``ok``, ``warn`` or ``fail``. ``run()``
returns them all; the CLI prints a table (or JSON) and exits 1 when anything failed. Nothing here
changes files or the database, and nothing needs the network.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclasses.dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""

    def as_dict(self):
        return dataclasses.asdict(self)


def _has(module):
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


# ------------------------------------------------------------------ the machine

def check_python():
    v = sys.version_info
    version = f"{v.major}.{v.minor}.{v.micro}"
    free = hasattr(sys, "_is_gil_enabled") and not sys._is_gil_enabled()
    if v < (3, 10):
        return Check("python", FAIL, f"Python {version}", "PyWeb needs Python 3.10 or newer")
    return Check("python", OK, f"Python {version}" + (" (free-threaded: one process uses every core)" if free else ""))


def check_pyweb():
    from . import __version__
    return Check("pyweb", OK, f"PyWeb {__version__} at {os.path.dirname(os.path.abspath(__file__))}")


def check_port(port=8000):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return Check("port", WARN, f"port {port} is in use", f"stop what uses it, or run pyweb dev --port {port + 1}")
    return Check("port", OK, f"port {port} is free for pyweb dev")


def check_pytest(app_dir):
    tests = [f for f in os.listdir(app_dir) if f.startswith("test_") and f.endswith(".py")] if os.path.isdir(app_dir) else []
    if not _has("pytest"):
        return Check("tests", WARN, "pytest isn't installed", "pip install pytest (pyweb test and pyweb_test use it)")
    if not tests:
        return Check("tests", WARN, "no test_*.py next to the app",
                     "add test_app.py using pyweb.testing.TestClient (pyweb new writes one)")
    return Check("tests", OK, f"{len(tests)} test file(s); run pyweb test")


def check_editor():
    from . import __version__
    code = shutil.which("code")
    if not code:
        return Check("editor", OK, "VS Code not found on PATH (any editor works; the extension adds checks as you type)")
    try:
        out = subprocess.run([code, "--list-extensions", "--show-versions"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return Check("editor", OK, "couldn't ask VS Code for its extensions")
    found = [line.split("@", 1)[1] for line in out.splitlines() if line.lower().startswith("maanavkrishna.pyweb@")]
    if not found:
        return Check("editor", WARN, "the PyWeb VS Code extension isn't installed",
                     "install it: code --install-extension maanavkrishna.pyweb")
    ext = found[0]
    if ext.split(".")[:2] != __version__.split(".")[:2]:
        return Check("editor", WARN, f"VS Code extension {ext} with PyWeb {__version__}",
                     "update the extension to the same minor version")
    return Check("editor", OK, f"VS Code extension {ext}")


# ------------------------------------------------------------------ the app

def _read(app):
    with open(app, encoding="utf-8") as fh:
        return fh.read()


def check_app(app):
    if not os.path.isfile(app):
        return Check("app", FAIL, f"no app at {app}", "pass the app file (pyweb doctor app.pyweb) or run pyweb new")
    from .compiler import compile_source
    from .compiler.errors import describe
    source = _read(app)
    try:
        out = compile_source(source, filename=app)
    except Exception as exc:  # noqa: BLE001 - any compile problem is the finding
        rec = describe(exc, source, app)
        where = f"{rec.get('file', app)}:{rec.get('line', '?')}"
        return Check("app", FAIL, f"{where}: {rec['message']}", rec.get("hint") or "run pyweb check for details")
    return Check("app", OK, f"{os.path.basename(app)} compiles: {len(out['pages'])} page(s), {len(out['rpc'])} server function(s)")


def check_extras(app):
    """Optional packages the app's source needs."""
    if not os.path.isfile(app):
        return []
    source = _read(app)
    wants = []
    if "encrypted=True" in source:
        wants.append(("cryptography", "encrypted fields", "pyweb-stack[crypto]"))
    if "push.send" in source or "from pyweb import push" in source or "pyweb.push" in source:
        wants.append(("cryptography", "web push", "pyweb-stack[push]"))
    if "desktop=" in source or "from pyweb import desktop" in source or "pyweb.desktop" in source:
        wants.append(("webview", "the desktop window (pyweb desktop)", "pyweb-stack[desktop]"))
    if "use_auth(" in source and "passkey" in source.lower():
        wants.append(("cryptography", "passkeys", "pyweb-stack[crypto]"))
    missing = [(mod, why, extra) for mod, why, extra in wants if not _has(mod)]
    if not missing:
        detail = "everything the app imports is installed" + ("" if _has("orjson") else
                                                               " (pyweb-stack[speed] would make JSON faster)")
        return [Check("extras", OK, detail)]
    return [Check("extras", FAIL, f"{why} needs the {mod} package", f"pip install '{extra}'")
            for mod, why, extra in missing]


def check_database(app):
    from .deploy import read_app
    if not os.path.isfile(app):
        return []
    app_dir = os.path.dirname(os.path.abspath(app))
    facts = read_app(app)
    url = os.environ.get("DATABASE_URL")
    if not facts.models and not url:
        return [Check("database", OK, "the app has no Models")]
    from .db import connect
    where, missing = "DATABASE_URL", False
    if not url:
        url = _app_database(app)
        where = url or "an in-memory SQLite database (data is lost on restart)"
        if url and url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
            path = os.path.join(app_dir, url[len("sqlite:///"):])
            missing = not os.path.exists(path)                # don't create it: pyweb dev will
            url = "sqlite:///" + path
    db = None
    if not missing:
        try:
            db = connect(url) if url else None
            if db is not None:
                db.execute("SELECT 1")
        except Exception as exc:  # noqa: BLE001 - unreachable database: report, don't crash
            return [Check("database", FAIL, f"can't connect to {where} ({type(exc).__name__}: {exc})",
                          "check the URL, that the server runs, and the driver extra (pyweb-stack[postgres|mysql])")]
    out = [Check("database", OK, f"{where} (created by pyweb dev on first run)" if missing else f"database: {where}")]
    migrations = os.path.join(app_dir, "migrations")
    if facts.models and not os.path.isdir(migrations):
        out.append(Check("migrations", WARN, "Models but no migrations/ (fine while prototyping)",
                         "before deploying: pyweb db diff --name initial"))
    elif os.path.isdir(migrations) and db is not None:
        from .db import migrate
        try:
            waiting = migrate.pending(db, migrations)
        except Exception as exc:  # noqa: BLE001
            return out + [Check("migrations", WARN, f"couldn't read migration state ({exc})", "pyweb db status")]
        if waiting:
            out.append(Check("migrations", WARN, f"{len(waiting)} not applied: {', '.join(m.label for m in waiting[:3])}",
                             "pyweb db upgrade"))
        else:
            out.append(Check("migrations", OK, "all migrations applied"))
    return out


def _app_database(app):
    """The literal ``App(database="...")`` in the app file, or None."""
    import re
    m = re.search(r"App\([^)]*?database\s*=\s*[\"']([^\"']+)[\"']", _read(app), re.S)
    return m.group(1) if m else None


def check_redis():
    url = os.environ.get("PYWEB_REDIS_URL")
    if not url:
        return []
    if not _has("redis"):
        return [Check("redis", FAIL, "PYWEB_REDIS_URL is set but the redis package isn't installed",
                      "pip install 'pyweb-stack[redis]'")]
    try:
        import redis
        redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2).ping()
    except Exception as exc:  # noqa: BLE001
        return [Check("redis", FAIL, f"can't reach PYWEB_REDIS_URL ({type(exc).__name__})",
                      "start Redis or fix the URL")]
    return [Check("redis", OK, "Redis answers")]


def check_secrets_in_source(app):
    if not os.path.isfile(app):
        return []
    from .secret_scan import scan_app
    found = scan_app(app)
    if not found:
        return [Check("secrets", OK, "no keys or tokens written in the source")]
    first = found[0]
    return [Check("secrets", FAIL, f"{len(found)} secret(s) in the source, e.g. {first.get('file', '')}:{first['line']}: "
                                   f"{first['message']}", "move them to environment variables")]


def check_dev_link(app):
    from . import devlink
    path = devlink.info_path(app)
    if not os.path.exists(path):
        return []
    if devlink.find(app) is None:
        return [Check("dev server", WARN, ".pyweb/dev.json points at a pyweb dev that isn't running",
                      f"delete {os.path.relpath(path)} (pyweb dev rewrites it on start)")]
    return [Check("dev server", OK, "pyweb dev is running (AI assistants can read its logs)")]


def check_upgrade(app):
    if not os.path.isfile(app):
        return []
    from . import upgrade
    todo = [f for f in upgrade.check(app) if getattr(f, "action", True)]
    if not todo:
        return [Check("upgrade", OK, "nothing to change for this PyWeb version")]
    first = todo[0]
    return [Check("upgrade", WARN, f"{len(todo)} change(s) for this version, e.g. {first.file}:{first.line}: "
                                   f"{first.message}", "pyweb upgrade --check (and --fix for the safe ones)")]


def check_ai(app):
    app_dir = os.path.dirname(os.path.abspath(app))
    have = [n for n in ("AGENTS.md", "CLAUDE.md", ".mcp.json") if os.path.exists(os.path.join(app_dir, n))]
    if not have:
        return [Check("ai", OK, "no AGENTS.md/.mcp.json (optional: pyweb new adds AGENTS.md; see the AI assistants docs)")]
    return [Check("ai", OK, f"assistant files: {', '.join(have)} (pyweb mcp serves the tools)")]


def check_production(app):
    from .cli import production_problems
    source = _read(app) if os.path.isfile(app) else ""
    problems = production_problems(source, os.path.dirname(os.path.abspath(app)))
    if not problems:
        return [Check("production", OK, "the environment is ready for production")]
    return [Check("production", FAIL, p, "") for p in problems]


# ------------------------------------------------------------------ running them

def run(app="app.pyweb", production=False, port=8000):
    checks = [check_python(), check_pyweb(), check_app(app)]
    for group in (check_extras(app), check_database(app), check_redis(), check_secrets_in_source(app),
                  check_upgrade(app), check_dev_link(app)):
        checks += group
    checks += [check_pytest(os.path.dirname(os.path.abspath(app))), check_port(port), check_editor()]
    checks += check_ai(app)
    if production:
        checks += check_production(app)
    return checks


MARK = {OK: "ok  ", WARN: "warn", FAIL: "FAIL"}


def render(checks, color=False):
    def paint(text, code):
        return f"\033[{code}m{text}\033[0m" if color else text
    colors = {OK: "32", WARN: "33", FAIL: "31"}
    width = max(len(c.name) for c in checks)
    lines = []
    for c in checks:
        lines.append(f"{paint(MARK[c.status], colors[c.status])}  {c.name:<{width}}  {c.detail}")
        if c.fix and c.status != OK:
            lines.append(f"      {' ' * width}  -> {c.fix}")
    fails = sum(c.status == FAIL for c in checks)
    warns = sum(c.status == WARN for c in checks)
    lines.append("")
    lines.append("all good" if not fails and not warns else f"{fails} problem(s), {warns} warning(s)")
    return "\n".join(lines)


def main(app="app.pyweb", production=False, as_json=False, port=8000):
    checks = run(app, production=production, port=port)
    if as_json:
        print(json.dumps({"ok": not any(c.status == FAIL for c in checks),
                          "checks": [c.as_dict() for c in checks]}, indent=2))
    else:
        print(render(checks, color=sys.stdout.isatty() and not os.environ.get("NO_COLOR")))
    return 1 if any(c.status == FAIL for c in checks) else 0
