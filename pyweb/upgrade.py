"""``pyweb upgrade``: find what an app needs to change for this version, and fix what's safe.

``check(app)`` reads the app and the files next to it (nothing is run) and
returns findings with file, line, what changed and what to do. ``fix(app)``
applies the rewrites that can't change behaviour:

* ``Model.configure("app.db")`` becomes ``App(database="sqlite:///app.db")``;
* ``pyweb db migrate`` / ``rollback`` become ``upgrade`` / ``downgrade``,
  and ``pyweb deploy --target X`` becomes ``pyweb deploy X``, in scripts,
  Procfiles, Dockerfiles, Makefiles and CI files.

Everything else is reported with what to do, because the right change
depends on the app (durable jobs instead of in-memory ones, tokens signed
with the raw secret, ...).
"""

from __future__ import annotations

import ast
import dataclasses
import os
import re

SCRIPT_FILES = ("Procfile", "Dockerfile", "Makefile", "justfile", "fly.toml", "render.yaml", "railway.json",
                "compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")
SCRIPT_EXT = (".sh", ".yml", ".yaml", ".toml", ".json", ".cfg", ".ini")
SKIP_DIRS = {".git", "node_modules", "dist", "build", "__pycache__", ".venv", "venv", "env", "static"}
_DB_ALIAS = re.compile(r"\b(pyweb\s+db\s+)(migrate|rollback)\b")
_DEPLOY_TARGET = re.compile(r"\b(pyweb\s+deploy)((?:\s+--[\w-]+(?:[ =][^\s-][^\s]*)?)*?)\s+--target[ =](\w+)")


@dataclasses.dataclass
class Finding:
    file: str
    line: int
    code: str
    message: str
    fix: str = ""                 # what --fix does, when it can
    action: bool = True           # False: worth knowing, nothing to change

    def as_dict(self):
        return dataclasses.asdict(self)


def _tree(path, text):
    from .compiler.parser import parse_source
    try:
        return parse_source(text, filename=path)[0] if path.endswith(".pyweb") else ast.parse(text, filename=path)
    except (SyntaxError, ValueError):
        return None


def _app_files(app_dir):
    for root, dirs, files in os.walk(app_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".") or d == ".github"]
        for fn in files:
            yield os.path.join(root, fn)


def _code_findings(path, text):
    out = []
    tree = _tree(path, text)
    if tree is None:
        return out
    rel = os.path.basename(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name, owner = node.func.attr, ast.unparse(node.func.value)
            if name == "configure" and (owner == "Model" or owner.endswith("Model")):
                out.append(Finding(rel, node.lineno, "model-configure",
                                   "Model.configure() is deprecated: give the app its database",
                                   fix='App(database="sqlite:///…") and remove this line'))
            if name in ("issue_session", "make_feed", "session_from_request") and len(node.args) >= 2 \
                    and not re.search(r"derive|sign_key|verify_keys", ast.unparse(node.args[1])):
                out.append(Finding(rel, node.lineno, "raw-secret",
                                   f"{name}() is given the raw secret: 0.5 only accepts keys derived per purpose, so "
                                   f"these tokens stop working. Use pyweb.keys.derive(secret, "
                                   f"\"{'feed' if name == 'make_feed' else 'session'}\") (or session.login())"))
        if isinstance(node, ast.ImportFrom) and node.module in ("pyweb.jobs", "pyweb.jobs.legacy"):
            old = [a.name for a in node.names if a.name in ("task", "Queue", "RedisQueue")]
            if old:
                out.append(Finding(rel, node.lineno, "memory-jobs",
                                   f"{', '.join(old)} {'keeps' if len(old) == 1 else 'keep'} jobs in memory (a restart loses them): use @app.job "
                                   "and .enqueue(...), which are durable and retried"))
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(".page") and node.args \
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == "/metrics":
            out.append(Finding(rel, node.lineno, "metrics-route",
                               "PyWeb now serves Prometheus metrics at /metrics; your page still wins, and the "
                               "metrics are at /__pyweb/metrics", action=False))
    return out


def _script_findings(path, text):
    out = []
    rel = os.path.basename(path)
    for i, line in enumerate(text.splitlines(), 1):
        m = _DB_ALIAS.search(line)
        if m:
            new = {"migrate": "upgrade", "rollback": "downgrade"}[m.group(2)]
            out.append(Finding(rel, i, "db-command", f"`pyweb db {m.group(2)}` is now `pyweb db {new}`",
                               fix=f"pyweb db {new}"))
        m = _DEPLOY_TARGET.search(line)
        if m:
            out.append(Finding(rel, i, "deploy-target", f"`pyweb deploy --target {m.group(3)}` is now "
                               f"`pyweb deploy {m.group(3)}` (and the new planner may add a worker, Postgres or "
                               "Redis: regenerate the files and compare)", fix=f"pyweb deploy {m.group(3)}"))
    return out


def _is_script(path):
    fn = os.path.basename(path)
    return fn in SCRIPT_FILES or fn.endswith(SCRIPT_EXT) or fn.startswith("Dockerfile")


def check(app="app.pyweb"):
    """Findings for the app at ``app`` (a file) and the project files next to it."""
    app_dir = os.path.dirname(os.path.abspath(app)) or "."
    out = []
    for path in _app_files(app_dir):
        if not (path.endswith((".pyweb", ".py")) or _is_script(path)):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        rel = os.path.relpath(path, app_dir)
        found = _code_findings(path, text) if path.endswith((".pyweb", ".py")) else _script_findings(path, text)
        for f in found:
            f.file = rel
            out.append(f)
    if os.path.isfile(app):
        with open(app, encoding="utf-8") as fh:
            source = fh.read()
        if "use_auth(" in source and not os.environ.get("PYWEB_ORIGIN"):
            out.append(Finding(os.path.basename(app), 0, "origin",
                               "the auth kit needs PYWEB_ORIGIN (the site's address) in production for the links "
                               "in its emails", action=False))
    return sorted(out, key=lambda f: (f.file, f.line))


def fix(app="app.pyweb"):
    """Apply the safe rewrites; returns ``{file: [what changed, ...]}``."""
    app_dir = os.path.dirname(os.path.abspath(app)) or "."
    changed = {}
    for path in _app_files(app_dir):
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        new, notes = text, []
        if path.endswith((".pyweb", ".py")):
            new, notes = _fix_configure(new)
        elif _is_script(path):
            new2 = _DB_ALIAS.sub(lambda m: m.group(1) + {"migrate": "upgrade", "rollback": "downgrade"}[m.group(2)], new)
            if new2 != new:
                notes.append("pyweb db migrate/rollback → upgrade/downgrade")
            new3 = _DEPLOY_TARGET.sub(lambda m: f"{m.group(1)} {m.group(3)}{m.group(2)}", new2)
            if new3 != new2:
                notes.append("pyweb deploy --target X → pyweb deploy X")
            new = new3
        if new != text:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            changed[os.path.relpath(path, app_dir)] = notes
    return changed


_CONFIGURE = re.compile(r"""^[ \t]*\w*Model\.configure\(\s*(?:path\s*=\s*)?(["'])([^"']*)\1\s*\)[ \t]*\n""", re.M)
_APP_CALL = re.compile(r"\bApp\(")


def _fix_configure(text):
    m = _CONFIGURE.search(text)
    if not m:
        return text, []
    path = m.group(2)
    url = "sqlite:///:memory:" if path in ("", ":memory:") else f"sqlite:///{path}"
    without = text[:m.start()] + text[m.end():]
    call = _APP_CALL.search(without)
    if call is None or "database=" in without[call.start():without.find(")", call.start()) + 1]:
        return text, []                    # no App(...) to give it to, or it already has one: leave it to a person
    inner_start = call.end()
    empty = without[inner_start:inner_start + 1] == ")"
    insert = f'database="{url}"' + ("" if empty else ", ")
    return without[:inner_start] + insert + without[inner_start:], [f'Model.configure("{path}") → App(database="{url}")']
