"""MCP server for PyWeb: lets AI assistants build, check, run, see and debug PyWeb apps.

Run ``pyweb mcp`` (stdio) or ``pyweb mcp --http`` (Streamable HTTP on 127.0.0.1:8765/mcp).
Register it with an AI tool, e.g.::

    claude mcp add pyweb -- pyweb mcp                 # Claude Code
    {"mcpServers": {"pyweb": {"command": "pyweb", "args": ["mcp"]}}}   # Cursor / Claude Desktop

Tools (paths are relative to the directory the server was started in): writing (guide, docs, new
app, check, explain, inspect, compiled, routes, ui, packages), running (render, call, screenshot,
review, test, profile, webhook_send), data (db_schema, db_query, migrations, jobs, requests, teams),
shipping (security_check, upgrade, deploy_plan, openapi) and the running ``pyweb dev`` (dev, logs).
Tools without the ``mcp_tools``/``devlink`` prefix live here; see ``TOOLS``.

Protocol 2025-11-25 (and older): tool titles, hints and output schemas, resources and resource
templates (docs, kit components, templates), prompts, argument completion, logging and progress.
Standard library only.
"""

from __future__ import annotations

import base64
import contextlib
import contextvars
import gzip
import json
import os
import re
import sys
import threading
import traceback

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = ("blank", "counter", "todo", "blog", "saas", "auth", "chat", "ai-chat", "desktop")

INSTRUCTIONS = (
    "PyWeb builds full-stack web apps from one .pyweb file (Python + markup). Before writing PyWeb code, "
    "call pyweb_guide; for any feature question call pyweb_docs (teams, forms, jobs, live data, deploys, "
    "installable and desktop apps, security). Build UIs from the kit (pyweb_ui lists components and makes "
    "themes). After every edit call pyweb_check and fix errors by line number using the hints; pyweb_explain "
    "answers 'where does this run / what can go here' for a line. Verify with pyweb_render and pyweb_call "
    "(as_user/org to act as a signed-in member), look with pyweb_screenshot or pyweb_review (light, dark, "
    "phone, accessibility), and run pyweb_test. Data: pyweb_db_schema, pyweb_db_query (read-only), "
    "pyweb_migrations after changing a Model, pyweb_jobs, pyweb_requests (SQL and N+1 warnings), "
    "pyweb_teams. When the user runs `pyweb dev`, pyweb_dev and pyweb_logs show its live errors and logs. "
    "Before shipping: pyweb_security_check (production=true), pyweb_upgrade, pyweb_deploy_plan. Prompts "
    "give step-by-step workflows (add_teams, design_pass, security_review, debug_page, ...)."
)

ERROR_HINTS = [
    ("only exists on the server", "Move that logic into an @server function and call it from the handler."),
    ("is not defined in browser code", "Define it at module level (a literal constant or a helper function), "
                                       "make it a page variable, or do the work in an @server function."),
    ("markup expressions must be", "Assign the server call's result to a page variable at the top of the page, "
                                   "or call it from an event handler."),
    ("derived/read-only", "Only assign to page variables the handler owns (state). Computed values update "
                          "automatically from their inputs."),
    ("would be sent to the browser", "Keep secrets inside @server functions; never read them in markup or handlers."),
    ("must name a local variable", "Declare the bound variable in the page first, e.g. `name = \"\"`."),
    ("must name a page variable", "bind= needs a page variable name, e.g. bind={name}."),
    ("unknown component", "Define it in this file (a capitalized function containing markup) or import it "
                          "from another .pyweb file: `from widgets import Card`."),
    ("is never closed", "Close the tag (or self-close void tags: <input ... />)."),
    ("mismatched </", "Make closing tags match the most recently opened tag."),
    ("unterminated tag or expression", "A tag or {expression} is missing its closing > or }."),
    ("is missing required prop", "Pass every required prop, or give the component parameter a default."),
    ("has no prop", "Use one of the component's parameter names as the attribute."),
    ("not supported in browser code", "Rewrite with supported Python (see pyweb_guide section 7) or move the code "
                                      "into an @server function."),
    ("must be called", "Call the server function inside a handler: `result = fn(...)`."),
    ("is a server function; call it from a", "Use a handler or lambda: onclick={lambda: fn(...)}"),
    ("isn't installed: run `pyweb add", "Run the `pyweb add` command from the message in the app folder, then "
                                       "check again."),
    ("which only exists in the browser", "Call the npm package in a handler or on_mount and keep the result in a "
                                         "page variable that the markup shows."),
    ("has no {children}", "Put {children} exactly once in the layout's markup where the page should go."),
    ("uses {children}", "Put {children} exactly once in the layout's markup where the page should go."),
    ("can only take `children`", "Layouts take only children; read request/session data in the layout body."),
    ("asks for layout", "Use the name of a function decorated with @app.layout, or layout=None for no layout."),
    ("npm() takes literal strings", "Bind at module level: Name = npm(\"package\") or npm(\"package\", \"Export\")."),
]


# ------------------------------------------------------------------ helpers

def _hint(message):
    for needle, hint in ERROR_HINTS:
        if needle in message:
            return hint
    return None


def _read_source(args):
    if args.get("source") is not None:
        return args["source"], args.get("path") or "app.pyweb"
    path = args.get("path") or "app.pyweb"
    with open(path, encoding="utf-8") as fh:
        return fh.read(), path


def _compile(args):
    from .compiler import compile_source
    source, filename = _read_source(args)
    return compile_source(source, filename=filename), source, filename


def _error_record(exc, filename):
    msg = getattr(exc, "pyweb_msg", None) or getattr(exc, "msg", None) or str(exc)
    if isinstance(msg, str) and msg.startswith("pyweb: "):
        msg = msg[len("pyweb: "):]
    line = getattr(exc, "lineno", None)
    rec = {"file": getattr(exc, "filename", None) or filename, "line": line, "message": str(msg)}
    hint = _hint(str(msg))
    if hint:
        rec["hint"] = hint
    return rec


def _gzip_size(js):
    from .build import minify_js
    return len(gzip.compress(minify_js(js).encode(), 9)) if js else 0


def _guide_text(section=None):
    with open(os.path.join(HERE, "ai", "guide.md"), encoding="utf-8") as fh:
        text = fh.read()
    if not section:
        return text
    parts = re.split(r"(?m)^## ", text)
    want = str(section).strip().lower()
    for part in parts[1:]:
        title = part.split("\n", 1)[0].lower()
        if title.startswith(want) or want in title:
            return "## " + part.strip()
    titles = [p.split("\n", 1)[0] for p in parts[1:]]
    return f"No section matching {section!r}. Sections: " + "; ".join(titles)


def _template_dir(name):
    """The folder of a multi-file template (``templates/<name>/``), or None."""
    path = os.path.join(HERE, "templates", name)
    return path if os.path.isdir(path) else None


def _template_source(name, title=None):
    if name == "blank":
        source = BLANK_APP
    else:
        folder = _template_dir(name)
        path = os.path.join(folder, "app.pyweb") if folder else os.path.join(HERE, "templates", f"{name}.pyweb")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
    if title:
        source = re.sub(r'App\(title="[^"]*"', f'App(title={json.dumps(title)}', source, count=1)
    return source


BLANK_APP = '''from pyweb import App

app = App(title="My app")


@app.page("/")
def Home():
    <main>
        <h1>Hello from PyWeb</h1>
    </main>
'''


def agent_instructions():
    """Contents of AGENTS.md written into new projects."""
    return (
        "# Instructions for AI coding agents\n\n"
        "This project is a PyWeb app (`pip install pyweb-stack`, `import pyweb`). The whole app is\n"
        "`app.pyweb`: Python plus HTML-like markup. Pages render on the server, event handlers\n"
        "compile to JavaScript, and `@server` functions run on the server.\n\n"
        "## Workflow\n\n"
        "- Run `pyweb check app.pyweb` after every change and fix errors by line number.\n"
        "- `pyweb inspect app.pyweb` shows what runs in the browser vs the server.\n"
        "- `pyweb dev app.pyweb` serves at http://localhost:8000 with live reload.\n"
        "- `pytest` runs `test_app.py`; add a test for each server function and page you change.\n"
        "- If the `pyweb` MCP server is available, use its tools: pyweb_guide and pyweb_docs to learn,\n"
        "  pyweb_check after each edit, pyweb_ui for components, pyweb_render/pyweb_call/pyweb_review to\n"
        "  verify, pyweb_test, and pyweb_dev/pyweb_logs for errors while `pyweb dev` runs.\n\n"
        + _guide_text().split("\n", 2)[2]
    )


STARTER_TEST = '''"""Tests for this app. Run them with `pytest` (or the pyweb_test MCP tool)."""

from pathlib import Path

import pytest

from pyweb import RPCError  # noqa: F401  (for testing server-function errors)
from pyweb.testing import TestClient

APP = str(Path(__file__).parent / "app.pyweb")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # keep files the app writes (e.g. SQLite) out of the project
    return TestClient(APP)


def test_home_page_renders(client):
    page = client.get("/")
    assert page.status in (200, 303), page.text[:500]


# Server functions are called exactly like the browser calls them:
#
# def test_save(client):
#     assert client.rpc("save", title="Hello") == ...
#     with pytest.raises(RPCError) as err:
#         client.rpc("save", title="")
#     assert err.value.code == "validation_error"
'''


def scaffold(directory, template="blank", title=None, overwrite=False):
    """Create a new app directory. Returns the list of files written."""
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template!r}; choose one of {', '.join(TEMPLATES)}")
    app_path = os.path.join(directory, "app.pyweb")
    if os.path.exists(app_path) and not overwrite:
        raise FileExistsError(f"{app_path} already exists (pass overwrite=true to replace it)")
    if title is None:
        title = os.path.basename(os.path.abspath(directory)).replace("-", " ").replace("_", " ").title()
    os.makedirs(os.path.join(directory, "static"), exist_ok=True)
    source = _template_source(template, title)
    files = {"app.pyweb": source,
             "test_app.py": STARTER_TEST,
             "AGENTS.md": agent_instructions(),
             "CLAUDE.md": "@AGENTS.md\n",
             ".gitignore": "/dist/\n__pycache__/\n*.db\n.env\n"}
    folder = _template_dir(template)
    if folder:                    # multi-file templates bring their own tests, seeds, ...
        for fn in sorted(os.listdir(folder)):
            if fn != "app.pyweb" and os.path.isfile(os.path.join(folder, fn)) and not fn.endswith(".pyc"):
                with open(os.path.join(folder, fn), encoding="utf-8") as fh:
                    files[fn] = fh.read()
    if "database=" in source:
        files["README.md"] = _readme(title, template, "seeds.py" in files)
    if "/static/app.css" in source:
        with open(os.path.join(HERE, "templates", "app.css"), encoding="utf-8") as fh:
            files["static/app.css"] = fh.read()
    written = []
    for rel, text in files.items():
        path = os.path.join(directory, rel)
        if rel != "app.pyweb" and os.path.exists(path) and not overwrite:
            continue
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        written.append(path)
    if "database=" in source and not os.path.isdir(os.path.join(directory, "migrations")):
        written += _initial_migration(directory)
    return written


def _initial_migration(directory):
    """``migrations/0001_initial.py`` for the template's Models (in a separate process, on a
    throwaway database, so nothing is created in the project and no Models leak into this one)."""
    import subprocess
    import tempfile
    app = os.path.abspath(os.path.join(directory, "app.pyweb"))
    out = os.path.abspath(os.path.join(directory, "migrations"))
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "PYWEB_WORKER": "0", "PYWEB_ENV": "development"}
        env.pop("DATABASE_URL", None)
        done = subprocess.run([sys.executable, "-m", "pyweb.cli", "db", "diff", "--app", app, "--database",
                               "sqlite:///:memory:", "--migrations", out, "--name", "initial"],
                              cwd=tmp, env=env, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeError(f"couldn't write the first migration: {(done.stderr or done.stdout).strip()[-500:]}")
    return sorted(os.path.join(out, f) for f in os.listdir(out) if f.endswith(".py"))


def _readme(title, template, has_seeds):
    seed = "pyweb db seed                # example data (see seeds.py)\n" if has_seeds else ""
    if template == "desktop":
        run = ('pip install "pyweb-stack[desktop]"\n'
               "pyweb desktop app.pyweb      # its own window; --dev reloads on save\n"
               "pyweb dev app.pyweb          # or in a browser, at http://localhost:8000\n")
    else:
        run = ('pip install "pyweb-stack[auth]"\n'
               "pyweb dev app.pyweb          # http://localhost:8000, reloads on save, with the dev toolbar\n")
    if template == "desktop":
        ship = ("pyweb build --desktop app.pyweb --package   # an installer for this OS\n```\n\n"
                "The data lives in the user's data folder; see the desktop guide for signing installers.\n")
    else:
        ship = ("pyweb deploy compose         # or: docker, k8s, fly, render, railway\n```\n\n"
                "`pyweb deploy` explains what it will run (database, Redis, workers) and why,\n"
                "then writes the files. `pyweb check --production` checks the settings.\n")
    return f"""# {title}

A [PyWeb](https://maanavkrishna.github.io/PyWeb/) app, from the `{template}` template.

```bash
{run}{seed}pytest                       # the tests in test_app.py
```

## Changing the data

Edit the Models in `app.pyweb`, then:

```bash
pyweb db diff                # writes migrations/NNNN_*.py from your changes (commit it)
pyweb db upgrade             # applies it (`pyweb dev` does this for you)
```

## Shipping it

```bash
{ship}"""


class _Apps:
    """TestClients per app path, reloaded when the file changes; cookies survive reloads."""

    def __init__(self):
        self._clients = {}

    def client(self, path):
        from .testing import TestClient
        path = os.path.abspath(path)
        entry = self._clients.get(path)
        files = entry[1].site.app.files if entry else [path]  # the app and the .pyweb files it imports
        mtime = tuple(os.path.getmtime(f) if os.path.exists(f) else 0 for f in files)
        if entry is None or entry[0] != mtime:
            cookies = entry[1].cookies if entry else {}
            with contextlib.redirect_stdout(sys.stderr):
                client = TestClient(path)
            client.cookies = dict(cookies)
            mtime = tuple(os.path.getmtime(f) for f in client.site.app.files)
            self._clients[path] = (mtime, client)
        return self._clients[path][1]


# -------------------------------------------------------------------- tools

def tool_guide(args):
    return _guide_text(args.get("section"))


def tool_new_app(args):
    directory = args.get("directory") or "."
    files = scaffold(directory, args.get("template") or "blank", args.get("title"),
                     bool(args.get("overwrite")))
    return {"created": files,
            "next_steps": [f"Edit {os.path.join(directory, 'app.pyweb')}",
                           "Call pyweb_check after each edit",
                           f"Run: pyweb dev {os.path.join(directory, 'app.pyweb')}"]}


def tool_check(args):
    from .security import check_source
    try:
        source, filename = _read_source(args)
    except OSError as exc:
        return {"ok": False, "errors": [{"file": args.get("path"), "line": None, "message": str(exc)}]}
    from .compiler import compile_source
    from .a11y import check as a11y_check
    findings = [{"line": f["line"], "kind": f["kind"], "message": f["message"]}
                for f in check_source(source, filename) + a11y_check(source, filename)]
    try:
        out = compile_source(source, filename=filename)
    except (SyntaxError, ValueError) as exc:
        return {"ok": False, "errors": [_error_record(exc, filename)], "findings": findings}
    pages = []
    for name, p in out["pages"].items():
        pages.append({"name": name, "route": p["route"], "signals": p["signals"],
                      "computeds": list(p["computeds"]), "interactive": bool(p["js"]),
                      "sent_to_browser": p.get("state_keys", []),
                      "page_js_gzip_bytes": _gzip_size(p["js"]),
                      **({"error_status": p["error_status"]} if p.get("error_status") else {}),
                      **({"layouts": p["layouts"]} if p.get("layouts") else {}),
                      **({"live": list(p["info"].live)} if getattr(p["info"], "live", None) else {}),
                      **({"npm": sorted(p["importmap"])} if p.get("importmap") else {})})
    blocking = [f for f in findings if f["kind"] in ("secret-leak",)]
    return {"ok": not blocking, "errors": [], "findings": findings, "pages": pages,
            "server_functions": [{"name": s["name"], "args": s["args"], "returns": s["returns"],
                                  **({"streams": True} if s.get("stream") else {})} for s in out["rpc"]],
            **({"layouts": {n: lay["prefix"] for n, lay in out["layouts"].items()}} if out.get("layouts") else {})}


def tool_openapi(args):
    """The app's server functions as an OpenAPI 3.1 document."""
    from .openapi import generate
    path = args.get("path") or "app.pyweb"
    with contextlib.redirect_stdout(sys.stderr):
        return generate(path, server_url=args.get("server_url"))


def tool_security_check(args):
    """Every security finding PyWeb can make about the app, in one list."""
    from .cli import production_problems
    from .lsp_data import parse
    from .lsp_teams import problems as team_problems
    from .secret_scan import scan_app
    from .security import check_source
    path = args.get("path") or "app.pyweb"
    source, filename = _read_source(args)
    findings = [{"kind": f["kind"], "line": f["line"], "message": f["message"], "file": f.get("file") or filename}
                for f in check_source(source, filename)]
    if os.path.exists(path):
        findings += [{"kind": f["kind"], "line": f["line"], "message": f["message"], "file": f.get("file") or path}
                     for f in scan_app(path)]
    tree, _ui = parse(source, filename)
    findings += [{"kind": code, "line": line, "message": msg, "file": filename}
                 for line, code, msg in team_problems(source, tree)]
    blocking = {"secret-leak", "hardcoded-secret", "tenant-unscoped"}
    res = {"ok": not any(f["kind"] in blocking for f in findings), "findings": findings,
           "threat_model": "https://github.com/MaanavKrishna/PyWeb/blob/main/THREAT_MODEL.md"}
    if args.get("production"):
        res["production"] = production_problems(source, os.path.dirname(os.path.abspath(path)))
        res["ok"] = res["ok"] and not res["production"]
    return res


def tool_routes(args):
    """Every URL the app answers: pages, their parameters (path or query, typed), layouts, error pages."""
    import ast
    out, _source, _filename = _compile(args)
    pages = []
    for name, p in out["pages"].items():
        info = p["info"]
        route = p["route"]
        params = []
        for a in info.node.args.args:
            default = info.defaults.get(a.arg)
            in_path = route is not None and "{" + a.arg + "}" in route
            params.append({"name": a.arg, "type": ast.unparse(a.annotation) if a.annotation else "str",
                           "from": "path" if in_path else ("error" if route is None else "query"),
                           **({"default": ast.unparse(default)} if default is not None else
                              {"required": True} if not in_path and route is not None else {})})
        entry = {"page": name, "params": params, "title": p.get("title"),
                 **({"route": route} if route is not None else {"error_status": p["error_status"]}),
                 **({"head": p["head"]} if p.get("head") else {}),
                 **({"layouts": p["layouts"]} if p.get("layouts") else {}),
                 "interactive": bool(p["js"])}
        if getattr(info, "live", None):
            entry["live"] = list(info.live)
        pages.append(entry)
    layouts = [{"layout": n, "prefix": lay["prefix"], "interactive": bool(lay["js"]),
                "pages": [p["page"] for p in pages if n in p.get("layouts", [])]}
               for n, lay in out.get("layouts", {}).items()]
    return {"pages": pages, "layouts": layouts,
            "server_functions": [{"name": s["name"], "endpoint": f"POST /__pyweb/rpc/{s['name']}",
                                  "streams": bool(s.get("stream"))} for s in out["rpc"]]}


def tool_packages(args):
    """Add, remove or list the npm packages an app uses in browser code."""
    from . import packages
    path = args.get("path") or "app.pyweb"
    app_dir = os.path.dirname(os.path.abspath(path)) if path.endswith(".pyweb") else os.path.abspath(path)
    add, remove = list(args.get("add") or []), list(args.get("remove") or [])
    try:
        if add or remove:
            with contextlib.redirect_stdout(sys.stderr):
                lock = packages.install(app_dir, add, remove=remove)
        else:
            lock = packages.read_lock(app_dir)
    except packages.PackageError as exc:
        return {"ok": False, "error": str(exc),
                "hint": "Pick a package that ships ES modules for browsers (e.g. lodash-es instead of lodash), "
                        "or do the work in an @server function."}
    out = {}
    for name, pkg in lock.get("packages", {}).items():
        types = pkg.get("types") or {}
        out[name] = {"version": pkg["version"], "direct": pkg.get("direct", False), "files": len(pkg.get("files", [])),
                     **({"exports": dict(list(types.items())[:40])} if types and pkg.get("direct") else {})}
    specs = sorted(lock.get("imports", {}))
    usage = [f'X = npm("{s}")  # default export; npm("{s}", "Name") for a named one, "*" for all' for s in specs
             if lock.get("packages", {}).get(packages.split_specifier(s)[0], {}).get("direct")]
    return {"ok": True, "app_dir": app_dir, "packages": out, "specifiers": specs, "usage": usage,
            "next_steps": ["from pyweb import npm, then bind at module level as shown in usage",
                           "use the bound names only in handlers / on_mount (not directly in markup)",
                           "commit pyweb.lock and static/vendor/"] if specs else []}


def tool_inspect(args):
    out, _source, _filename = _compile(args)
    pages = {}
    for name, p in out["pages"].items():
        pages[name] = {
            "route": p["route"],
            "placement": {sym: {"runs": loc, "why": why} for sym, (loc, why) in p["placement"].items()},
            "sent_to_browser": p.get("state_keys", []),
        }
    layouts = {name: {"prefix": lay["prefix"],
                      "placement": {sym: {"runs": loc, "why": why} for sym, (loc, why) in lay["info"].reasons.items()},
                      "sent_to_browser": list(lay["info"].sent)}
               for name, lay in out.get("layouts", {}).items()}
    return {"pages": pages, **({"layouts": layouts} if layouts else {}),
            "rpc_endpoints": [f"POST /__pyweb/rpc/{s['name']}" for s in out["rpc"]]}


def tool_compiled(args):
    out, _source, _filename = _compile(args)
    pages = out["pages"]
    name = args.get("page") or next(iter(pages), None)
    if name not in pages:
        return {"error": f"no page {name!r}; pages: {', '.join(pages)}"}
    p = pages[name]
    what = args.get("what") or "both"
    res = {"page": name}
    if what in ("js", "both"):
        res["javascript"] = p["js"] or "// no JavaScript: this page is static"
    if what in ("html", "both"):
        res["server_html"] = p["html_body"]
    return res


APPS = _Apps()


def _sign_in(client, args):
    """``as_user`` (an email) signs in first, with ``roles`` and, for apps with organizations, ``org``
    (a name: created if missing) and ``org_role``."""
    email = args.get("as_user")
    if email:
        client.logout()
        client.login(email, roles=tuple(args.get("roles") or ()), org=args.get("org"),
                     org_role=args.get("org_role") or "owner")
    elif args.get("org"):
        raise ValueError("org needs as_user (who is working in it)")


def tool_render(args):
    path = args.get("path") or "app.pyweb"
    url = args.get("url") or "/"
    with contextlib.redirect_stdout(sys.stderr):
        client = APPS.client(path)
        _sign_in(client, args)
        resp = client.get(url)
    text = resp.text
    limit = int(args.get("max_chars") or 20000)
    res = {"status": resp.status, "content_type": resp.header("Content-Type")}
    if resp.header("Location"):
        res["redirect_to"] = resp.header("Location")
    if resp.status >= 500:
        pre = re.search(r"<pre[^>]*>(.*?)</pre>", text, re.S)
        if pre:
            import html as _html
            res["error"] = _html.unescape(pre.group(1)).strip()[-4000:]
    title = re.search(r"<title>(.*?)</title>", text, re.S)
    if title:
        import html as _html
        res["title"] = _html.unescape(title.group(1))
        head = {m.group(1): _html.unescape(m.group(2)) for m in re.finditer(
            r'<(?:meta|link) data-pw-head (?:name|property|rel)="([^"]+)" (?:content|href)="([^"]*)"', text)}
        if head:
            res["head"] = head
        roots = re.findall(r'data-pw-root="(\w+)"', text)
        if roots:
            res["roots"] = roots          # layouts first, then the page
    res["body"] = text[:limit] + ("\n... (truncated)" if len(text) > limit else "")
    return res


def tool_call(args):
    from .rpc import RPCError
    path = args.get("path") or "app.pyweb"
    fn = args.get("function")
    if not fn:
        raise ValueError("function is required")
    client = APPS.client(path)
    streams = any(s["name"] == fn and s.get("stream") for s in client.app.compiled["rpc"]) or any(
        s["name"] == fn and s.get("stream") for lib in client.app.libraries for s in lib.rpc)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            _sign_in(client, args)
            result = client.rpc(fn, **(args.get("args") or {}))
        if streams:
            return {"ok": True, "streamed": True, "chunks": result, "cookies": sorted(client.cookies)}
        return {"ok": True, "result": result, "cookies": sorted(client.cookies)}
    except RPCError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": str(exc), "status": exc.status}}


def tool_screenshot(args):
    """Open a page in headless Chromium, run steps, return a PNG and what the page shows."""
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        raise RuntimeError("pyweb_screenshot needs Playwright: pip install playwright && "
                           "python -m playwright install chromium") from None
    # Playwright's sync API can't run inside an event loop or another sync
    # session on the same thread, so the browser gets a thread of its own.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(contextvars.copy_context().run, _screenshot, args).result()


def _screenshot(args):
    from playwright.sync_api import sync_playwright

    from .mcp_tools import _target
    path = args.get("path") or "app.pyweb"
    url = args.get("url") or "/"
    width, height = int(args.get("width") or 1280), int(args.get("height") or 800)
    result = {"steps": []}
    with _target(path, args) as (base, cookies), sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            ctx = browser.new_context(viewport={"width": width, "height": height},
                                      color_scheme=args.get("scheme") or "light")
            if cookies:
                ctx.add_cookies([{"name": k, "value": v, "url": base} for k, v in cookies.items()])
            page = ctx.new_page()
            console = []
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: console.append(f"error: {e}"))
            resp = page.goto(base + url)
            result["status"] = resp.status if resp else None
            if page.query_selector("#pw-state"):
                page.wait_for_selector("[data-pw-ready]", state="attached", timeout=10000)
            steps = args.get("steps") or []
            for i, step in enumerate(steps):
                progress(i, len(steps), f"step {i + 1}: {step.get('action')} {step.get('selector') or ''}".strip())
                action, sel, value = step.get("action"), step.get("selector"), step.get("value")
                try:
                    if action == "click":
                        page.click(sel, timeout=5000)
                    elif action == "fill":
                        page.fill(sel, value or "", timeout=5000)
                    elif action == "press":
                        page.press(sel or "body", value or "Enter", timeout=5000)
                    elif action == "select":
                        page.select_option(sel, value, timeout=5000)
                    elif action == "goto":
                        page.goto(base + (value or "/"))
                    elif action == "wait":
                        if sel:
                            page.wait_for_selector(sel, timeout=int(value or 5000))
                        else:
                            page.wait_for_timeout(int(value or 500))
                    else:
                        raise ValueError(f"unknown action {action!r}")
                    page.wait_for_load_state("networkidle")
                    result["steps"].append({"step": i, "ok": True})
                except Exception as exc:  # noqa: BLE001 - report the failing step, still screenshot
                    what = f"{action} {sel}" if sel else str(action)
                    result["steps"].append({"step": i, "ok": False,
                                            "error": f"{what}: {str(exc).splitlines()[0]}"})
                    break
            root = page.query_selector("[data-pw-root]")
            result.update({
                "url": page.url[len(base):] or "/",
                "title": page.title(),
                "target": "dev" if not cookies and _is_dev(base, path) else "fresh",
                "hydration": root.get_attribute("data-pw-mode") if root else None,
                "console": console,
                "text": page.inner_text("body")[: int(args.get("max_chars") or 4000)],
                "__image__": page.screenshot(full_page=bool(args.get("full_page"))),
            })
        finally:
            browser.close()
    return result


def _is_dev(base, path):
    from . import devlink
    dev = devlink.find(path)
    return bool(dev) and dev["url"].rstrip("/") == base.rstrip("/")


def tool_test(args):
    """Run the app's pytest tests and summarise the result."""
    import importlib.util
    import subprocess
    if importlib.util.find_spec("pytest") is None:
        raise RuntimeError("pytest is not installed: pip install pytest")
    target = os.path.abspath(args.get("path") or ".")
    cwd = target if os.path.isdir(target) else os.path.dirname(target)
    cmd = [sys.executable, "-m", "pytest", target, "-q", "-rfE", "--no-header", "--color=no", "-p", "no:cacheprovider"]
    if args.get("filter"):
        cmd += ["-k", args["filter"]]
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    timer = threading.Timer(int(args.get("timeout") or 300), proc.kill)
    timer.start()
    lines = []
    try:
        for line in proc.stdout:                      # pytest -q prints "....   [ 40%]" as it goes
            lines.append(line)
            pct = re.search(r"\[\s*(\d+)%\]\s*$", line)
            if pct:
                progress(int(pct.group(1)), 100, "running tests")
        proc.wait()
    finally:
        timer.cancel()
    out = "".join(lines).strip()
    counts = {k: int(n) for n, k in re.findall(r"(\d+) (passed|failed|errors?|skipped)", out.splitlines()[-1] if out else "")}
    res = {"ok": proc.returncode == 0, "passed": counts.get("passed", 0), "failed": counts.get("failed", 0),
           "errors": counts.get("error", 0) + counts.get("errors", 0), "skipped": counts.get("skipped", 0),
           "summary": out.splitlines()[-1] if out else ""}
    if proc.returncode == 5:
        res["hint"] = ("No tests found. Add test_app.py next to app.pyweb using pyweb.testing.TestClient "
                       "(new apps from pyweb_new_app include one).")
    limit = int(args.get("max_chars") or 8000)
    res["output"] = out[-limit:]
    return res


# ------------------------------------------------------------- data, jobs, requests

_READ_ONLY_SQL = re.compile(r"^\s*(select|with|explain|values|show|describe|pragma\s+table_info)\b", re.I)
_WRITE_WORDS = re.compile(r"\b(insert|update|delete|replace|merge|drop|alter|create|truncate|grant|revoke|attach|"
                          r"detach|vacuum|reindex|copy|call|do|lock|set|begin|commit|rollback|savepoint)\b", re.I)


def _app_db(args):
    """``(client, database, models)`` for the app at ``path`` (loaded like the dev server loads it)."""
    from . import models as M
    client = APPS.client(args.get("path") or "app.pyweb")
    db = M.database()
    if db is None:
        raise RuntimeError("this app has no database (App(database=...) or DATABASE_URL)")
    return client, db, client.site.app.models()


def _model_info(model):
    meta = model._meta
    fields = []
    for name, f in meta.fields.items():
        rel = getattr(f, "relation", None)
        entry = {"name": name, "type": f.kind, "nullable": bool(f.nullable)}
        for key in ("unique", "private", "readonly"):
            if getattr(f, key, False):
                entry[key] = True
        if rel is not None:
            entry["references"] = rel.to.__name__ if isinstance(rel.to, type) else str(rel.to)
        rules = getattr(f, "rules", None)
        if rules is not None:
            entry.update({k: v for k, v in rules.schema().items() if k not in ("kind",)})
        fields.append(entry)
    return {"model": model.__name__, "table": meta.table, "fields": fields,
            "many_to_many": sorted(meta.m2m), "reverse": sorted(meta.reverse),
            "policy": bool(getattr(model, "__pyweb_policy__", None) or getattr(meta, "policy", None)),
            "tenant": bool(getattr(meta, "tenant", False))}


def tool_db_schema(args):
    from .db import schema as S
    from .db.dialect import dialect_of
    _, db, models = _app_db(args)
    live = S.introspect(db)
    plan = S.diff(live, S.from_models(models), dialect_of(db))
    return {"dialect": dialect_of(db).name,
            "models": [_model_info(m) for m in models],
            "tables": {name: [{"name": c.name, "type": c.kind, "nullable": c.nullable,
                               **({"primary_key": True} if c.primary_key else {}),
                               **({"references": c.references[0]} if c.references else {})}
                              for c in t.columns] for name, t in sorted(live.items())},
            "drift": [op.describe() for op in plan.ops] if plan else [],
            "hint": "drift lists what `pyweb db diff` would write" if plan else "the database matches the Models"}


def tool_db_query(args):
    """Read-only SQL with a row cap; any write is refused and everything is rolled back."""
    sql = (args.get("sql") or "").strip().rstrip(";").strip()
    if not sql:
        raise ValueError("pass sql, e.g. SELECT * FROM users LIMIT 10")
    if ";" in sql or not _READ_ONLY_SQL.match(sql) or _WRITE_WORDS.search(re.sub(r"'(?:[^']|'')*'", "''", sql)):
        raise ValueError("only single read-only statements (SELECT, WITH, EXPLAIN) are allowed here; "
                         "change data through the app's server functions or a migration")
    limit = max(1, min(int(args.get("limit") or 100), 1000))
    _, db, _ = _app_db(args)
    from .db.dialect import dialect_of
    dialect = dialect_of(db).name
    params = tuple(args.get("params") or ())

    class _Rollback(Exception):
        pass
    out = {}
    try:
        with db.transaction():
            if dialect == "postgres":
                db.execute("SET TRANSACTION READ ONLY")
            elif dialect == "sqlite":
                db.execute("PRAGMA query_only = ON")
            try:
                capped = sql
                if re.match(r"\s*(select|with|values)\b", sql, re.I):
                    # The database stops at the cap, rather than sending a huge table here.
                    capped = f"SELECT * FROM ({sql}) pw_q LIMIT {limit + 1}"
                res = db.execute(capped, params)
                cols, rows = list(res.columns), res.fetchall()[:limit + 1]
            finally:
                if dialect == "sqlite":
                    db.execute("PRAGMA query_only = OFF")
            from .ssr import to_jsonable
            out = {"columns": cols, "rows": [to_jsonable(list(r)) for r in rows[:limit]],
                   "truncated": len(rows) > limit}
            raise _Rollback()
    except _Rollback:
        pass
    from .telemetry.logs import redact
    out["rows"] = [[redact(v, c) for v, c in zip(row, out["columns"])] for row in out["rows"]]
    return out


def tool_migrations(args):
    from .db import migrate as MG
    client, db, models = _app_db(args)
    folder = client.site.app.migrations_dir or os.path.join(
        os.path.dirname(os.path.abspath(args.get("path") or "app.pyweb")), "migrations")
    action = args.get("action") or "status"
    if action == "status":
        return {"folder": folder, "migrations": [{"label": label, "applied": done}
                                                 for label, done in MG.status(db, folder)],
                "report": MG.report(db, folder, models)}
    if action == "diff":
        paths, plan = MG.make_migration(db, models, folder, args.get("name"),
                                        allow_destructive=bool(args.get("allow_destructive")))
        return {"written": paths, "changes": [op.describe() for op in plan.ops] if plan else [],
                "notes": list(getattr(plan, "notes", []) or []),
                "next": "review the file, then call pyweb_migrations with action=upgrade" if paths else
                        "no changes: the database matches the Models"}
    if action == "upgrade":
        return {"applied": MG.upgrade(db, folder, contract=bool(args.get("contract")))}
    raise ValueError("action must be status, diff or upgrade")


def tool_jobs(args):
    from .jobs import core
    APPS.client(args.get("path") or "app.pyweb")
    store = core.backend()
    if args.get("retry"):
        return {"retried": {job_id: bool(store.retry(job_id)) for job_id in args["retry"]}}
    if args.get("run"):
        from .jobs import Worker
        return {"ran": Worker(concurrency=2, schedule=False).drain(timeout=float(args.get("timeout") or 30))}
    rows = store.list(state=args.get("state"), limit=int(args.get("limit") or 30), name=args.get("name"))
    for r in rows:
        if r.get("last_error"):
            r["last_error"] = r["last_error"][-1500:]
    return {"store": type(store).__name__,
            "counts": store.counts() if hasattr(store, "counts") else None,
            "jobs": [{k: v for k, v in r.items() if k != "trace"} for r in rows],
            "registered": sorted(core.REGISTRY)}


def tool_requests(args):
    """What the last requests (pyweb_render / pyweb_call) did: SQL, N+1 warnings, spans, jobs, errors."""
    from . import devlink, telemetry
    limit = int(args.get("limit") or 10)
    source = args.get("source") or "auto"
    items = list(telemetry.RECENT)[-limit:] if source != "dev" else []
    target = "mcp"
    if source == "dev" or (source == "auto" and not items):
        dev = devlink.dev_requests(args.get("path") or "app.pyweb", limit)
        if dev is not None:
            items, target = dev, "dev"
    if not args.get("sql", True):
        items = [{k: v for k, v in r.items() if k != "sql"} for r in items]
    return {"requests": items, "target": target,
            "hint": "requests appear here after pyweb_render or pyweb_call (or, with source=dev, what the running "
                    "pyweb dev served); a warning about a repeated query means add .include(...) or move the "
                    "query out of the loop"}


PATH_PROP = {"type": "string", "description": "Path to the .pyweb file (default: app.pyweb)."}
SOURCE_PROP = {"type": "string", "description": "Source text to check instead of reading `path`."}

TOOLS = [
    {"name": "pyweb_guide", "fn": tool_guide, "readOnly": True,
     "description": "The rules for writing PyWeb (.pyweb) apps: skeleton, what runs where, state, markup, "
                    "components, server functions, the supported browser Python subset, and common errors with "
                    "fixes. Read it before writing PyWeb code.",
     "inputSchema": {"type": "object", "properties": {
         "section": {"type": "string", "description": "Optional section title or number, e.g. 'markup' or '8'."}}}},
    {"name": "pyweb_new_app", "fn": tool_new_app, "readOnly": False,
     "description": "Create a new PyWeb app directory from a template, including AGENTS.md/CLAUDE.md instructions "
                    "for AI agents. Templates: blank, counter, todo (components, lists), blog (SQL database, "
                    "server functions, route params), saas (accounts, Models with policies, forms, live pages, "
                    "jobs, admin, migrations: the best start for a real product), auth (sessions, passwords), "
                    "chat (live updates), ai-chat (streaming AI replies with Stop and Markdown). New apps "
                    "include test_app.py.",
     "inputSchema": {"type": "object", "properties": {
         "directory": {"type": "string", "description": "Directory to create (default: current directory)."},
         "template": {"type": "string", "enum": list(TEMPLATES)},
         "title": {"type": "string", "description": "App title."},
         "overwrite": {"type": "boolean", "description": "Replace an existing app.pyweb."}}}},
    {"name": "pyweb_check", "fn": tool_check, "readOnly": True,
     "description": "Compile a PyWeb app and run the security and accessibility checks. Returns ok, errors "
                    "(file, line, message, hint), findings, pages (route, signals, computeds, what is sent to the browser, JS size) and server "
                    "functions. Call after every edit.",
     "inputSchema": {"type": "object", "properties": {"path": PATH_PROP, "source": SOURCE_PROP}}},
    {"name": "pyweb_inspect", "fn": tool_inspect, "readOnly": True,
     "description": "Explain where every page variable, handler and server function runs (browser or server) and "
                    "why, and which values are sent to the browser.",
     "inputSchema": {"type": "object", "properties": {"path": PATH_PROP, "source": SOURCE_PROP}}},
    {"name": "pyweb_compiled", "fn": tool_compiled, "readOnly": True,
     "description": "Show the JavaScript the compiler generated for a page and/or its server-rendered HTML "
                    "(static prerender). Useful for debugging browser behaviour.",
     "inputSchema": {"type": "object", "properties": {
         "path": PATH_PROP, "source": SOURCE_PROP,
         "page": {"type": "string", "description": "Page function name (default: first page)."},
         "what": {"type": "string", "enum": ["js", "html", "both"]}}}},
    {"name": "pyweb_render", "fn": tool_render, "readOnly": False,
     "description": "Request a URL from the app in-process (like a browser's first load) and return the status, "
                    "redirect target, server-rendered HTML, and the Python traceback for 500 errors. Runs the "
                    "app's server code. Session cookies from pyweb_call are reused.",
     "inputSchema": {"type": "object", "properties": {
         "path": PATH_PROP, "url": {"type": "string", "description": "URL path, e.g. / or /posts/1?x=y"},
         "as_user": {"type": "string", "description": "Sign in as this email first (with the auth kit, the "
                                                   "account is created if needed)."},
         "roles": {"type": "array", "items": {"type": "string"}, "description": "Roles for as_user."},
         "org": {"type": "string", "description": "With organizations: work in this organization (by name; "
                                                 "created if missing) as as_user."},
         "org_role": {"type": "string", "enum": ["owner", "admin", "member", "viewer"],
                      "description": "as_user's role in org (default owner)."},
         "max_chars": {"type": "integer", "description": "Truncate the body (default 20000)."}}}},
    {"name": "pyweb_call", "fn": tool_call, "readOnly": False,
     "description": "Call an @server function of the app exactly like the browser does (JSON RPC with "
                    "validation). Returns the result (for functions that yield: every streamed chunk) or the "
                    "typed error (code, message). Cookies persist across "
                    "calls, so e.g. call a login function, then pyweb_render a protected page.",
     "inputSchema": {"type": "object", "required": ["function"], "properties": {
         "path": PATH_PROP, "function": {"type": "string"},
         "as_user": {"type": "string", "description": "Sign in as this email first (with the auth kit, the "
                                                   "account is created if needed)."},
         "roles": {"type": "array", "items": {"type": "string"}, "description": "Roles for as_user."},
         "org": {"type": "string", "description": "With organizations: work in this organization (by name; "
                                                 "created if missing) as as_user."},
         "org_role": {"type": "string", "enum": ["owner", "admin", "member", "viewer"],
                      "description": "as_user's role in org (default owner)."},
         "args": {"type": "object", "description": "Keyword arguments by parameter name."}}}},
    {"name": "pyweb_openapi", "fn": tool_openapi, "readOnly": True,
     "description": "The app's server functions as an OpenAPI 3.1 document (paths, argument schemas from the type "
                    "hints and Field rules, the guards each one has), as `pyweb openapi` writes it.",
     "inputSchema": {"type": "object", "properties": {
         "path": PATH_PROP, "server_url": {"type": "string", "description": "The API's base URL, if known."}}}},
    {"name": "pyweb_security_check", "fn": tool_security_check, "readOnly": True,
     "description": "Security findings for the app in one list: secrets reaching the browser, hard-coded keys in "
                    "the app's files, unsafe patterns, tenant Models read across organizations (.unscoped() "
                    "outside system()), bad rate limits and roles; with production=true also what the "
                    "environment is missing (secrets, HTTPS cookies, proxies). Links THREAT_MODEL.md.",
     "inputSchema": {"type": "object", "properties": {
         "path": PATH_PROP, "source": SOURCE_PROP,
         "production": {"type": "boolean", "description": "Also check the environment for production."}}}},
    {"name": "pyweb_routes", "fn": tool_routes, "readOnly": True,
     "description": "Map of the app: every page with its route, parameters (from the path or the query "
                    "string, with types and defaults), title and head tags, the layouts that wrap it, live-data "
                    "variables, error pages, and server functions (marking those that stream).",
     "inputSchema": {"type": "object", "properties": {"path": PATH_PROP, "source": SOURCE_PROP}}},
    {"name": "pyweb_packages", "fn": tool_packages, "readOnly": False,
     "description": "npm packages for browser code, without Node.js. With `add` (e.g. ['chart.js/auto', "
                    "'canvas-confetti@^1']) or `remove`, downloads/removes them into static/vendor/ and pyweb.lock; "
                    "with neither, lists what is installed. Returns versions, exported names (from TypeScript "
                    "declarations) and the npm(...) lines to bind them.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "The app's .pyweb file or folder (default: app.pyweb)."},
         "add": {"type": "array", "items": {"type": "string"}, "description": "Packages to add, e.g. lit or ethers@^6."},
         "remove": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "pyweb_screenshot", "fn": tool_screenshot, "readOnly": False,
     "description": "See the app in a real browser (headless Chromium): open a URL, optionally run steps (click, "
                    "fill, press, select, goto, wait), and get a PNG screenshot plus the page text, console errors "
                    "and whether the page hydrated. Use it to check layout and interactions after edits. Needs "
                    "Playwright (pip install playwright && python -m playwright install chromium).",
     "inputSchema": {"type": "object", "properties": {
         "path": PATH_PROP, "url": {"type": "string", "description": "URL path to open (default /)."},
         "steps": {"type": "array", "description": "Actions to perform before the screenshot, in order.",
                   "items": {"type": "object", "required": ["action"], "properties": {
                       "action": {"type": "string", "enum": ["click", "fill", "press", "select", "goto", "wait"]},
                       "selector": {"type": "string", "description": "CSS selector or text=..."},
                       "value": {"type": "string", "description": "Text to fill, key to press, option, URL, "
                                                                  "or wait time in ms."}}}},
         "width": {"type": "integer"}, "height": {"type": "integer"},
         "scheme": {"type": "string", "enum": ["light", "dark"]},
         "full_page": {"type": "boolean"}, "max_chars": {"type": "integer"},
         "as_user": {"type": "string", "description": "Open the page signed in as this email."},
         "roles": {"type": "array", "items": {"type": "string"}},
         "org": {"type": "string"}, "org_role": {"type": "string", "enum": ["owner", "admin", "member", "viewer"]},
         "target": {"type": "string", "enum": ["auto", "fresh"],
                    "description": "auto (default) uses a running pyweb dev for this app; fresh starts a server."}}}},
    {"name": "pyweb_db_schema", "fn": tool_db_schema, "readOnly": True,
     "description": "The app's data: every Model (fields, types, rules, relations, policies) and the tables "
                    "the database has now, plus drift (what `pyweb db diff` would change). Read this before "
                    "writing queries or changing Models.",
     "inputSchema": {"type": "object", "properties": {"path": PATH_PROP}}},
    {"name": "pyweb_db_query", "fn": tool_db_query, "readOnly": True,
     "description": "Run one read-only SQL statement (SELECT/WITH/EXPLAIN) on the app's database and get rows "
                    "back (capped; secrets redacted). Writes are refused and the transaction is rolled back.",
     "inputSchema": {"type": "object", "required": ["sql"], "properties": {
         "path": PATH_PROP, "sql": {"type": "string"},
         "params": {"type": "array", "description": "Values for ? / %s placeholders."},
         "limit": {"type": "integer", "description": "Rows to return (default 100, max 1000)."}}}},
    {"name": "pyweb_migrations", "fn": tool_migrations, "readOnly": False,
     "description": "Migrations: status (applied/pending and drift), diff (write the next migration from the "
                    "Models; removals go to a separate contract step) or upgrade (apply pending ones to the "
                    "development database).",
     "inputSchema": {"type": "object", "properties": {
         "path": PATH_PROP, "action": {"type": "string", "enum": ["status", "diff", "upgrade"]},
         "name": {"type": "string", "description": "diff: the migration's name."},
         "allow_destructive": {"type": "boolean"}, "contract": {"type": "boolean"}}}},
    {"name": "pyweb_jobs", "fn": tool_jobs, "readOnly": False,
     "description": "Background jobs: list them (filter by state/name) with errors and counts, retry failed "
                    "ones by id, or run what's queued now (run=true) to test a job end to end.",
     "inputSchema": {"type": "object", "properties": {
         "path": PATH_PROP, "state": {"type": "string", "enum": ["queued", "running", "done", "failed", "dead"]},
         "name": {"type": "string"}, "limit": {"type": "integer"},
         "retry": {"type": "array", "items": {"type": "string"}}, "run": {"type": "boolean"},
         "timeout": {"type": "number"}}}},
    {"name": "pyweb_requests", "fn": tool_requests, "readOnly": True,
     "description": "What the app did for the last requests made with pyweb_render/pyweb_call, or (source=dev) "
                    "for the browser's requests to the running `pyweb dev`: time, status, every SQL statement, "
                    "N+1 warnings, spans, jobs queued, emails and errors (the dev toolbar's data). Use it to find "
                    "slow or repeated queries.",
     "inputSchema": {"type": "object", "properties": {
         "path": PATH_PROP, "limit": {"type": "integer"},
         "source": {"type": "string", "enum": ["auto", "mcp", "dev"],
                    "description": "auto: this server's requests, or the dev server's when there are none."},
         "sql": {"type": "boolean", "description": "Include SQL (default true)."}}}},
    {"name": "pyweb_test", "fn": tool_test, "readOnly": False,
     "description": "Run the app's tests with pytest (test_*.py files using pyweb.testing.TestClient) and return "
                    "pass/fail counts, the summary line and the failure output.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Test file or directory (default: current directory)."},
         "filter": {"type": "string", "description": "Only run tests matching this pytest -k expression."},
         "max_chars": {"type": "integer"}, "timeout": {"type": "integer"}}}},
]

def _arg(name, description, required=False):
    return {"name": name, "description": description, "required": required}


_PATH_ARG = _arg("path", "The app (default: app.pyweb).")

PROMPTS = [
    {"name": "build_pyweb_app", "title": "Build an app",
     "description": "Build a PyWeb app from a description, verifying it with the PyWeb tools.",
     "arguments": [_arg("description", "What the app should do.", True),
                   _arg("directory", "Where to create it (default: current directory).")]},
    {"name": "add_ai_feature", "title": "Add a streaming AI feature",
     "description": "Add a streaming AI feature (chat, summarise, generate) to an existing PyWeb app.",
     "arguments": [_arg("feature", "What the AI feature should do.", True), _PATH_ARG]},
    {"name": "make_data_live", "title": "Make pages live",
     "description": "Make pages of an existing PyWeb app update by themselves when the database changes.",
     "arguments": [_PATH_ARG]},
    {"name": "add_teams", "title": "Add teams",
     "description": "Turn a single-user app into one where teams share data: organizations, invitations, "
                    "roles, tenant Models and tests that prove teams can't see each other's rows.",
     "arguments": [_PATH_ARG]},
    {"name": "make_installable", "title": "Make it installable",
     "description": "Make the app installable on phones and desktops (a PWA): manifest, icons, offline page, "
                    "optionally Web Push.",
     "arguments": [_PATH_ARG, _arg("push", "yes to add push notifications too.")]},
    {"name": "make_desktop_app", "title": "Make a desktop app",
     "description": "Run the app in its own desktop window with native file dialogs, then package an installer.",
     "arguments": [_PATH_ARG]},
    {"name": "security_review", "title": "Security review",
     "description": "Review the app against PyWeb's threat model and fix what it finds.",
     "arguments": [_PATH_ARG]},
    {"name": "upgrade_pyweb", "title": "Upgrade PyWeb",
     "description": "Bring an app written for an older PyWeb up to date: findings, safe rewrites, "
                    "migrations and tests.",
     "arguments": [_PATH_ARG]},
    {"name": "design_pass", "title": "Design pass with the UI kit",
     "description": "Make the app look and feel polished with the UI kit, a theme and motion, checked for "
                    "accessibility in light and dark mode and on a phone.",
     "arguments": [_PATH_ARG, _arg("goal", "What should change (e.g. 'calmer, more spacious').")]},
    {"name": "fix_failing_test", "title": "Fix a failing test",
     "description": "Find out why a test fails and fix the app (not the test, unless the test is wrong).",
     "arguments": [_PATH_ARG, _arg("test", "The test's name or a -k expression (default: every test).")]},
    {"name": "debug_page", "title": "Debug a page",
     "description": "Find out why a page errors, looks wrong or is slow, using the running dev server when "
                    "there is one.",
     "arguments": [_PATH_ARG, _arg("url", "The page's URL path, e.g. /projects/1.", True),
                   _arg("problem", "What's wrong (optional).")]},
]


def _steps(intro, *steps):
    return intro + "\n\nSteps:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))


def _prompt_text(name, args):
    path = args.get("path") or "app.pyweb"
    if name == "add_ai_feature":
        return _steps(
            f"Add this AI feature to the PyWeb app at `{path}`: {args.get('feature', '').strip()}",
            "Call pyweb_guide with section 'streaming' and follow it; read the ai-chat template "
            "(resource pyweb://templates/ai-chat) for a provider-agnostic model call.",
            "Write a @server function that yields text as the model produces it. Read API keys from "
            "os.environ inside server code only; validate and trim what the browser sends; add "
            "rate=\"20/hour\" so one person can't run up the bill.",
            "In the page, read it with `async for` in an `async def` handler, show the text with "
            "<Markdown text={...} />, and add a Stop button that calls `.cancel()` on the stream.",
            "Call pyweb_check until it is clean, pyweb_call the function (it returns the streamed chunks) "
            "and look at the page with pyweb_screenshot.",
            "Add a test with TestClient.rpc (it returns the list of chunks) and run pyweb_test.")
    if name == "make_data_live":
        return _steps(
            f"Make the PyWeb app at `{path}` live: pages should update by themselves when the data changes.",
            "Call pyweb_docs with query 'live' (or read pyweb://docs/live-data) and pyweb_routes to see the pages.",
            "For Models, end the page's query with .live(): `tasks = Task.where(project_id=pid).order(\"id\").live()`. "
            "For raw SQL, use live(db, sql, params) from pyweb. Keep each query small (limit) and filtered per user.",
            "Remove polling (setInterval) and hand-written publish/subscribe that only refresh data.",
            "Make sure writes go through Models or pyweb.db (or call db.notify('table') after other writes).",
            "Call pyweb_check, then verify with pyweb_screenshot: change data with pyweb_call and look again.")
    if name == "add_teams":
        return _steps(
            f"Add teams to the PyWeb app at `{path}`: people work in organizations and only see their team's data.",
            "Read pyweb://docs/teams. Call pyweb_db_schema to see the Models.",
            "Switch to app.use_auth(orgs=True). Add auth.plans({...}) if the product has plans (True/False values "
            "are features, numbers are limits such as seats).",
            "Add tenant=True to every Model whose rows belong to a team (class Project(Model, tenant=True)). Drop "
            "row policies that only compared owner ids; keep ones that restrict within a team.",
            "Make sure a signed-in person has a team: auth.org(), or auth.create_org(...) then auth.switch_org(...) "
            "on their first visit (see the saas template, pyweb://templates/saas).",
            "Guard admin actions with @server(org_role=[\"owner\", \"admin\"]) and pages with "
            "@app.page(..., org_role=...); paid features with feature=\"name\".",
            "Link /orgs (members and invitations) and /account/tokens from the navigation.",
            "Run pyweb_migrations (diff, then upgrade) and pyweb_security_check; fix any tenant-unscoped finding.",
            "Check isolation with pyweb_render/pyweb_call using as_user and org for two different organizations.",
            "Add tests: client.login(\"ann@example.com\", org=\"Acme\") and client.login(\"bo@example.com\", "
            "org=\"Globex\") must not see each other's rows; a teammate (org_role=\"member\") must. Run pyweb_test.")
    if name == "make_installable":
        push = str(args.get("push") or "").lower() in ("yes", "true", "1", "y")
        steps = [
            "Read pyweb://docs/pwa-push.",
            "Add pwa=True to App(...), or pwa=PWA(name=..., icon=\"static/icon.png\", theme_color=..., "
            "cache_pages=True) (from pyweb.pwa import PWA). A square PNG of 512 px or more makes the icons.",
            "pyweb_render /manifest.webmanifest and check the name, colours and icons; pyweb_screenshot the "
            "page and confirm the console has no service worker errors.",
            "Use platform.pwa (from pyweb import platform) only for layout tweaks, never for security."]
        if push:
            steps += ["Add Web Push: pip install 'pyweb-stack[push]'; ask permission only from a button "
                      "(data-pw-push=\"subscribe\"), and send with push.send(user, title, body, url) from server "
                      "code. Set PYWEB_VAPID_KEY in production (pyweb push key)."]
        steps += ["Run pyweb_security_check with production=true and pyweb_test."]
        return _steps(f"Make the PyWeb app at `{path}` installable.", *steps)
    if name == "make_desktop_app":
        return _steps(
            f"Make the PyWeb app at `{path}` a desktop app.",
            "Read pyweb://docs/desktop (and the desktop template, pyweb://templates/desktop).",
            "Add desktop=Window(width=..., height=..., min_size=...) to App(...) (from pyweb.desktop import Window). "
            "Keep the database as sqlite:///name.db: in the desktop app it lives in the user's data folder.",
            "Desktop-only features (pyweb.desktop.open_file, save_file, notify, reveal) are server-side Python: "
            "call them from @server functions, and show their buttons only when platform.desktop is true.",
            "Test with pyweb_call and monkeypatch the dialog functions in tests; run pyweb_test.",
            "Tell the user to try `pyweb desktop app.pyweb` and to build an installer with "
            "`pyweb build --desktop app.pyweb --package` (signing is described in the docs).")
    if name == "security_review":
        return _steps(
            f"Review the PyWeb app at `{path}` for security problems, and fix them.",
            "Read pyweb://threat-model for what PyWeb already defends against; don't re-solve those.",
            "Call pyweb_security_check with production=true and fix every finding, blocking ones first.",
            "Call pyweb_db_schema: every Model with people's data needs a row policy or tenant=True; fields "
            "nobody should read back need private=True; sensitive values encrypted=True.",
            "Read each @server function (pyweb_routes lists them): it should check who may call it (login=True, "
            "roles=, org_role=), validate its arguments (types and Field rules), and rate-limit anything "
            "expensive or abusable (rate=\"10/minute\").",
            "Look for outbound requests to URLs users choose: they must use pyweb.http.fetch.",
            "Check isolation with pyweb_call/pyweb_render as two different users (as_user) and organizations.",
            "Add a test for each fix and run pyweb_test. Summarise what you changed and what's left.")
    if name == "upgrade_pyweb":
        return _steps(
            f"Upgrade the PyWeb app at `{path}` to this version of PyWeb.",
            "Read pyweb://docs/upgrading.",
            "Call pyweb_upgrade to list the findings, then pyweb_upgrade with fix=true for the safe rewrites; "
            "make the remaining changes by hand.",
            "Call pyweb_migrations with action=status; if it reports drift (e.g. the auth_sessions table), "
            "action=diff then action=upgrade.",
            "Call pyweb_check and pyweb_security_check, then pyweb_test. Fix what fails.",
            "Look at the main pages with pyweb_review: the sign-in and admin pages now follow the app's theme.")
    if name == "design_pass":
        goal = (args.get("goal") or "").strip()
        return _steps(
            f"Give the PyWeb app at `{path}` a design pass with the UI kit" + (f": {goal}" if goal else "."),
            "Call pyweb_review on the main pages to see them now (light, dark, phone) and their accessibility.",
            "Call pyweb_ui with action=list, then action=show for the components you need (Card, Button, Dialog, "
            "DataTable, Sidebar...). Replace hand-made markup with kit components; keep ids that tests use.",
            "Pick a theme: pyweb_ui action=theme with accent/radius/font (or a preset) and save it into App(theme=...).",
            "Add motion where it helps understanding: animate=\"fade-up\" on content, initial/animate/exit and "
            "layout on list rows, a Dialog for forms. Don't animate everything.",
            "Call pyweb_check (its a11y findings too) and pyweb_review again; fix contrast, labels and focus problems.",
            "Run pyweb_test.")
    if name == "fix_failing_test":
        test = (args.get("test") or "").strip()
        return _steps(
            f"Fix the failing test{f' {test!r}' if test else 's'} of the PyWeb app at `{path}`.",
            "Run pyweb_test" + (f" with filter={test!r}" if test else "") + " and read the failure output.",
            "Reproduce it outside the test: pyweb_render the page or pyweb_call the function with the same user "
            "(as_user, org) and arguments; pyweb_requests shows the SQL and errors of those requests.",
            "Find the cause in the app (pyweb_explain on the line, pyweb_inspect for where things run). Fix the "
            "app; change the test only if it asserts something wrong.",
            "Call pyweb_check, then pyweb_test until everything passes.")
    if name == "debug_page":
        url = args.get("url") or "/"
        problem = (args.get("problem") or "").strip()
        return _steps(
            f"Debug {url} in the PyWeb app at `{path}`" + (f": {problem}" if problem else "."),
            "Call pyweb_dev: if a dev server is running, its errors and logs are the real ones (pyweb_logs).",
            f"pyweb_render {url} (a 500 includes the traceback) and pyweb_screenshot it (console errors, "
            "hydration).",
            "pyweb_requests shows each request's SQL (N+1 warnings), time and errors; pyweb_profile shows where "
            "a slow page spends its time.",
            "Fix the cause, pyweb_check, and confirm with pyweb_render/pyweb_screenshot. Add a test (pyweb_test).")
    desc = args.get("description", "").strip()
    directory = args.get("directory") or "."
    return _steps(
        f"Build a PyWeb web app in `{directory}`: {desc}",
        "Call pyweb_guide and follow its rules. pyweb_docs answers questions about any feature.",
        f"Call pyweb_new_app (directory={directory!r}) with the closest template (saas for products with "
        "accounts and teams, desktop for a desktop app).",
        "Edit app.pyweb. Keep database/secret/import work in @server functions; keep handlers to "
        "browser-safe Python. Build the UI from pyweb.ui components (pyweb_ui lists them). Use a layout for "
        "shared navigation, .live() for data that changes, a yielding @server function for AI replies, and "
        "pyweb_packages for JavaScript libraries.",
        "Call pyweb_check after every edit until ok is true with no errors; apply the hints.",
        "Verify with pyweb_routes, pyweb_render (each page) and pyweb_call (each server function), and look at "
        "the result with pyweb_screenshot or pyweb_review (light, dark, phone, accessibility).",
        "Add tests to test_app.py and run them with pyweb_test; run pyweb_security_check.",
        "Tell the user to run `pyweb dev app.pyweb` to try it.")


# ------------------------------------------------------------------ tool metadata

def _obj(required=(), **props):
    """A JSON object schema (extra keys allowed) with these properties."""
    return {"type": "object", "properties": props, **({"required": list(required)} if required else {})}


_S, _I, _B, _N = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}, {"type": "number"}
_A, _O, _ANY = {"type": "array"}, {"type": "object"}, {}
_S0, _I0 = {"type": ["string", "null"]}, {"type": ["integer", "null"]}

# title, output schema and hints for the tools above (new tools in mcp_tools.py declare their own).
_META = {
    "pyweb_guide": {"title": "Read the PyWeb guide"},
    "pyweb_new_app": {"title": "Create an app from a template", "idempotent": False,
                      "output": _obj(["created"], created=_A, next_steps=_A)},
    "pyweb_check": {"title": "Compile and check an app",
                    "output": _obj(["ok", "errors"], ok=_B, errors=_A, findings=_A, pages=_A,
                                   server_functions=_A, layouts=_O)},
    "pyweb_inspect": {"title": "What runs where",
                      "output": _obj(["pages"], pages=_O, layouts=_O, rpc_endpoints=_A)},
    "pyweb_compiled": {"title": "Generated JavaScript and HTML",
                       "output": _obj(page=_S, javascript=_S, server_html=_ANY, error=_S)},
    "pyweb_render": {"title": "Request a page", "idempotent": False,
                     "output": _obj(["status"], status=_I, content_type=_S0, redirect_to=_S, error=_S, title=_S,
                                    head=_O, roots=_A, body=_S)},
    "pyweb_call": {"title": "Call a server function", "idempotent": False,
                   "output": _obj(["ok"], ok=_B, result=_ANY, streamed=_B, chunks=_A, cookies=_A, error=_O)},
    "pyweb_openapi": {"title": "OpenAPI document", "output": _obj(["openapi"], openapi=_S, paths=_O, info=_O)},
    "pyweb_security_check": {"title": "Security review",
                             "output": _obj(["ok", "findings"], ok=_B, findings=_A, threat_model=_S,
                                            production=_A)},
    "pyweb_routes": {"title": "Map of pages and functions",
                     "output": _obj(["pages"], pages=_A, layouts=_A, server_functions=_A)},
    "pyweb_packages": {"title": "npm packages", "idempotent": False, "openWorld": True,
                       "output": _obj(["ok"], ok=_B, error=_S, hint=_S, app_dir=_S, packages=_O, specifiers=_A,
                                      usage=_A, next_steps=_A)},
    "pyweb_screenshot": {"title": "Screenshot in a real browser", "idempotent": False,
                         "output": _obj(["steps"], steps=_A, status=_I0, url=_S, title=_S, hydration=_S0,
                                        console=_A, text=_S, target=_S)},
    "pyweb_db_schema": {"title": "Models and tables",
                        "output": _obj(["models"], dialect=_S, models=_A, tables=_O, drift=_A, hint=_S)},
    "pyweb_db_query": {"title": "Read-only SQL",
                       "output": _obj(["columns", "rows"], columns=_A, rows=_A, truncated=_B)},
    "pyweb_migrations": {"title": "Migrations", "idempotent": False, "destructive": False,
                         "output": _obj(folder=_S, migrations=_A, report=_ANY, written=_A, changes=_A, notes=_A,
                                        next=_S, applied=_ANY)},
    "pyweb_jobs": {"title": "Background jobs", "idempotent": False,
                   "output": _obj(store=_S, counts=_ANY, jobs=_A, registered=_A, retried=_O, ran=_ANY, target=_S)},
    "pyweb_requests": {"title": "Recent requests", "output": _obj(["requests"], requests=_A, hint=_S, target=_S)},
    "pyweb_test": {"title": "Run the tests", "idempotent": False,
                   "output": _obj(["ok"], ok=_B, passed=_I, failed=_I, errors=_I, skipped=_I, summary=_S,
                                  output=_S, hint=_S)},
}
for _t in TOOLS:
    _t.update(_META.get(_t["name"], {}))

from .mcp_tools import TOOLS as _MORE_TOOLS  # noqa: E402 - new tools use the helpers above

TOOLS = TOOLS + _MORE_TOOLS


def tool_listing(t):
    """How ``tools/list`` describes a tool."""
    read_only = bool(t["readOnly"])
    out = {"name": t["name"], "title": t.get("title", t["name"]), "description": t["description"],
           "inputSchema": t["inputSchema"],
           "annotations": {"title": t.get("title", t["name"]), "readOnlyHint": read_only,
                           "destructiveHint": bool(t.get("destructive", False)) and not read_only,
                           "idempotentHint": bool(t.get("idempotent", read_only)),
                           "openWorldHint": bool(t.get("openWorld", False))}}
    if t.get("output"):
        out["outputSchema"] = t["output"]
    return out


_JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list,
               "object": dict}


def check_arguments(schema, args):
    """Problems with ``args`` against a tool's input schema (required keys, types, enums)."""
    if not isinstance(args, dict):
        return ["arguments must be an object"]
    problems = [f"missing required argument {k!r}" for k in schema.get("required", []) if k not in args]
    props = schema.get("properties", {})
    for key, value in args.items():
        spec = props.get(key)
        if spec is None:
            problems.append(f"unknown argument {key!r} (known: {', '.join(sorted(props)) or 'none'})")
            continue
        want = spec.get("type")
        types = want if isinstance(want, list) else [want] if want else []
        if value is None and "null" in types:
            continue
        ok = not types or any(t in _JSON_TYPES and isinstance(value, _JSON_TYPES[t])
                              and not (t in ("integer", "number") and isinstance(value, bool)) for t in types)
        if not ok:
            problems.append(f"{key!r} should be {' or '.join(types)}, not {type(value).__name__}")
        elif "enum" in spec and value not in spec["enum"]:
            problems.append(f"{key!r} must be one of {', '.join(map(str, spec['enum']))}")
    return problems


# ------------------------------------------------------------------ progress and log notifications

_CALL = contextvars.ContextVar("pyweb_mcp_call", default=None)
LOG_LEVELS = ("debug", "info", "notice", "warning", "error", "critical", "alert", "emergency")


def progress(done, total=None, message=None):
    """Tell the client how far the running tool call is (when it asked, with a progress token)."""
    call = _CALL.get()
    if not call or call.get("token") is None:
        return
    params = {"progressToken": call["token"], "progress": done}
    if total is not None:
        params["total"] = total
    if message:
        params["message"] = message
    call["server"].send({"jsonrpc": "2.0", "method": "notifications/progress", "params": params})


def log(level, message, **data):
    """A log message to the client (shown at or above the level it set with logging/setLevel)."""
    call = _CALL.get()
    if call:
        call["server"].log(level, message, **data)


# ------------------------------------------------------------------ resources

def docs_dir():
    """The documentation folder: shipped in the package, or ``docs/`` in a source checkout."""
    for path in (os.path.join(HERE, "ai", "docs"), os.path.join(os.path.dirname(HERE), "docs")):
        if os.path.isdir(path) and any(f.endswith(".md") for f in os.listdir(path)):
            return path
    return None


def doc_pages():
    """``{slug: (path, title)}``: ``docs/30-teams.md`` is ``teams``."""
    folder = docs_dir()
    out = {}
    if folder is None:
        return out
    for fn in sorted(os.listdir(folder)):
        m = re.fullmatch(r"(?:\d+-)?([\w-]+)\.md", fn)
        if not m or fn == "THREAT_MODEL.md":
            continue
        path = os.path.join(folder, fn)
        with open(path, encoding="utf-8") as fh:
            first = fh.readline()
        out[m.group(1)] = (path, first.lstrip("# ").strip() or m.group(1))
    return out


def threat_model_path():
    for path in (os.path.join(HERE, "ai", "docs", "THREAT_MODEL.md"),
                 os.path.join(os.path.dirname(HERE), "THREAT_MODEL.md")):
        if os.path.isfile(path):
            return path
    return None


def resources():
    out = [{"uri": "pyweb://guide", "name": "guide", "title": "PyWeb guide for AI assistants",
            "mimeType": "text/markdown", "description": "Rules, patterns and error fixes for writing .pyweb apps."},
           {"uri": "pyweb://docs", "name": "docs", "title": "PyWeb documentation (index)", "mimeType": "text/markdown",
            "description": "Every documentation page, with its pyweb://docs/<page> address."},
           {"uri": "pyweb://kit", "name": "kit", "title": "UI kit components (index)", "mimeType": "text/markdown",
            "description": "Every pyweb.ui component with its variants and sizes."}]
    if threat_model_path():
        out.append({"uri": "pyweb://threat-model", "name": "threat-model", "title": "Threat model",
                    "mimeType": "text/markdown",
                    "description": "Every threat PyWeb defends against, its mitigation and the test that proves it."})
    for slug, (_path, title) in doc_pages().items():
        out.append({"uri": f"pyweb://docs/{slug}", "name": f"docs/{slug}", "title": title, "mimeType": "text/markdown",
                    "description": f"Documentation: {title}."})
    for t in TEMPLATES:
        out.append({"uri": f"pyweb://templates/{t}", "name": f"templates/{t}", "title": f"Template: {t}",
                    "mimeType": "text/plain", "description": f"The {t} starter app (app.pyweb)."})
    return out


RESOURCE_TEMPLATES = [
    {"uriTemplate": "pyweb://docs/{page}", "name": "docs-page", "title": "Documentation page",
     "mimeType": "text/markdown", "description": "A documentation page by name, e.g. pyweb://docs/teams."},
    {"uriTemplate": "pyweb://kit/{component}", "name": "kit-component", "title": "UI kit component",
     "mimeType": "text/markdown",
     "description": "A pyweb.ui component's source, parameters and allowed values, e.g. pyweb://kit/Dialog."},
    {"uriTemplate": "pyweb://templates/{name}", "name": "template", "title": "Starter app",
     "mimeType": "text/plain", "description": "A pyweb new template's app.pyweb, e.g. pyweb://templates/saas."},
]


def read_resource(uri):
    if uri == "pyweb://guide":
        return _guide_text(), "text/markdown"
    if uri == "pyweb://docs":
        lines = ["# PyWeb documentation", ""] + [f"- [{title}](pyweb://docs/{slug})"
                                                 for slug, (_p, title) in doc_pages().items()]
        return "\n".join(lines) + "\n", "text/markdown"
    if uri == "pyweb://threat-model" and threat_model_path():
        with open(threat_model_path(), encoding="utf-8") as fh:
            return fh.read(), "text/markdown"
    if uri == "pyweb://kit":
        from .mcp_tools import kit_index_markdown
        return kit_index_markdown(), "text/markdown"
    m = re.fullmatch(r"pyweb://docs/([\w-]+)", uri)
    if m:
        pages = doc_pages()
        slug = m.group(1)
        slug = slug if slug in pages else next((s for s in pages if s.endswith(slug) or slug.endswith(s)), None)
        if slug:
            with open(pages[slug][0], encoding="utf-8") as fh:
                return fh.read(), "text/markdown"
    m = re.fullmatch(r"pyweb://kit/(\w+)", uri)
    if m:
        from .mcp_tools import kit_component_markdown
        text = kit_component_markdown(m.group(1))
        if text:
            return text, "text/markdown"
    m = re.fullmatch(r"pyweb://templates/([\w-]+)", uri)
    if m and m.group(1) in TEMPLATES:
        return _template_source(m.group(1)), "text/plain"
    raise KeyError(uri)


def complete(ref, argument):
    """Values for ``completion/complete``: prompt arguments and resource template parameters."""
    name, value = argument.get("name"), str(argument.get("value") or "")
    options = []
    if ref.get("type") == "ref/resource":
        uri = ref.get("uri", "")
        if uri.startswith("pyweb://docs/"):
            options = list(doc_pages())
        elif uri.startswith("pyweb://kit/"):
            from .compiler.pipeline import kit_index
            options = sorted(n for n in kit_index() if n[:1].isupper())
        elif uri.startswith("pyweb://templates/"):
            options = list(TEMPLATES)
    elif ref.get("type") == "ref/prompt":
        if name in ("path", "app"):
            options = sorted(f for f in _walk_files(".") if f.endswith(".pyweb"))
        elif name == "directory":
            options = sorted(d for d in os.listdir(".") if os.path.isdir(d) and not d.startswith("."))
        elif name == "template":
            options = list(TEMPLATES)
        elif name == "target":
            options = ["docker", "compose", "k8s", "fly", "render", "railway"]
    low = value.lower()
    hits = [o for o in options if o.lower().startswith(low)] + [o for o in options
                                                                if low in o.lower() and not o.lower().startswith(low)]
    return {"values": hits[:100], "total": len(hits), "hasMore": len(hits) > 100}


def _walk_files(root, limit=2000):
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "dist", "__pycache__",
                                                                         "venv", "env", "build")]
        out += [os.path.relpath(os.path.join(dirpath, f), root) for f in files]
        if len(out) > limit:
            break
    return out


# ------------------------------------------------------------------ protocol

class Server:
    """The MCP server: one per connection. ``send`` writes a message (a notification) to the client."""

    def __init__(self, send=None):
        from . import __version__
        self.version = __version__
        self.protocol = PROTOCOL_VERSIONS[0]
        self.log_level = "info"
        self._send = send
        self._lock = threading.Lock()

    def send(self, msg):
        if self._send is not None:
            with self._lock:
                self._send(msg)

    def log(self, level, message, **data):
        if level not in LOG_LEVELS or LOG_LEVELS.index(level) < LOG_LEVELS.index(self.log_level):
            return
        self.send({"jsonrpc": "2.0", "method": "notifications/message",
                   "params": {"level": level, "logger": "pyweb", "data": {"message": message, **data}}})

    def handle(self, msg):
        """Handle one JSON-RPC message; return the response dict or None."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _error(None, -32600, "invalid request")
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if mid is None:  # notifications (initialized, cancelled, ...) and responses need no answer
            return None
        try:
            result = self.dispatch(method, params)
        except _RPCFault as fault:
            return _error(mid, fault.code, fault.message)
        except Exception as exc:  # noqa: BLE001 - report, never crash the server
            return _error(mid, -32603, f"{type(exc).__name__}: {exc}")
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def dispatch(self, method, params):
        if method == "initialize":
            requested = params.get("protocolVersion")
            self.protocol = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return {"protocolVersion": self.protocol,
                    "capabilities": {"tools": {"listChanged": False},
                                     "resources": {"listChanged": False, "subscribe": False},
                                     "prompts": {"listChanged": False},
                                     "completions": {}, "logging": {}},
                    "serverInfo": {"name": "pyweb", "title": "PyWeb", "version": self.version,
                                   "websiteUrl": "https://maanavkrishna.github.io/PyWeb/", "icons": [ICON]},
                    "instructions": INSTRUCTIONS}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [tool_listing(t) for t in TOOLS]}
        if method == "tools/call":
            return self.call_tool(params.get("name"), params.get("arguments") or {},
                                  (params.get("_meta") or {}).get("progressToken"))
        if method == "resources/list":
            return {"resources": resources()}
        if method == "resources/templates/list":
            return {"resourceTemplates": RESOURCE_TEMPLATES}
        if method == "resources/read":
            uri = params.get("uri")
            try:
                text, mime = read_resource(uri)
            except KeyError:
                raise _RPCFault(-32002, f"resource not found: {uri}") from None
            return {"contents": [{"uri": uri, "mimeType": mime, "text": text}]}
        if method == "prompts/list":
            return {"prompts": [{k: v for k, v in p.items() if k != "text"} for p in PROMPTS]}
        if method == "prompts/get":
            prompt = next((p for p in PROMPTS if p["name"] == params.get("name")), None)
            if prompt is None:
                raise _RPCFault(-32602, f"unknown prompt {params.get('name')!r}")
            args = params.get("arguments") or {}
            missing = [a["name"] for a in prompt.get("arguments", []) if a.get("required") and not args.get(a["name"])]
            if missing:
                raise _RPCFault(-32602, f"prompt {prompt['name']!r} needs: {', '.join(missing)}")
            text = _prompt_text(prompt["name"], args)
            return {"description": prompt["description"],
                    "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}
        if method == "completion/complete":
            return {"completion": complete(params.get("ref") or {}, params.get("argument") or {})}
        if method == "logging/setLevel":
            level = params.get("level")
            if level not in LOG_LEVELS:
                raise _RPCFault(-32602, f"level must be one of {', '.join(LOG_LEVELS)}")
            self.log_level = level
            return {}
        raise _RPCFault(-32601, f"method not found: {method}")

    def call_tool(self, name, args, token=None):
        tool = next((t for t in TOOLS if t["name"] == name), None)
        if tool is None:
            raise _RPCFault(-32602, f"unknown tool {name!r}")
        problems = check_arguments(tool["inputSchema"], args)
        if problems:
            return {"content": [{"type": "text", "text": "Invalid arguments: " + "; ".join(problems)}],
                    "isError": True}
        reset = _CALL.set({"server": self, "token": token})
        try:
            with contextlib.redirect_stdout(sys.stderr):
                value = tool["fn"](args)
        except Exception as exc:  # noqa: BLE001 - tool errors are results, not protocol errors
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            return {"content": [{"type": "text", "text": detail}], "isError": True}
        finally:
            _CALL.reset(reset)
        if isinstance(value, str):
            return {"content": [{"type": "text", "text": value}], "isError": False}
        images = []
        if isinstance(value, dict):
            image = value.pop("__image__", None)
            images = [image] if image is not None else list(value.pop("__images__", None) or [])
        content = [{"type": "text", "text": json.dumps(value, indent=2, default=str)}]
        for image in images:
            content.append({"type": "image", "mimeType": "image/png",
                            "data": base64.b64encode(image).decode("ascii")})
        structured = json.loads(json.dumps(value, default=str))
        return {"content": content, "structuredContent": structured, "isError": False}


ICON = {"mimeType": "image/svg+xml", "sizes": ["any"],
        "src": "data:image/svg+xml;base64," + base64.b64encode(
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" '
            b'fill="#4f46e5"/><path d="M10 23V9h7a5 5 0 0 1 0 10h-7" fill="none" stroke="#fff" stroke-width="3" '
            b'stroke-linecap="round" stroke-linejoin="round"/></svg>').decode()}


class _RPCFault(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve_stdio(stdin=None, stdout=None):
    """Run the server until stdin closes."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    def write(msg):
        stdout.write(json.dumps(msg, default=str) + "\n")
        stdout.flush()

    server = Server(send=write)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            responses = [_error(None, -32700, "parse error")]
        else:
            msgs = msg if isinstance(msg, list) else [msg]
            responses = [r for r in (server.handle(m) for m in msgs) if r is not None]
        for resp in responses:
            server.send(resp)


def main():
    serve_stdio()


if __name__ == "__main__":
    main()
