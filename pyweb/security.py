"""Security: HTML escaping, CSRF, taint checks, secret-flow analysis,
path traversal, redirects, error sanitizing, app scanner."""

from __future__ import annotations

import ast
import html as _html
import os
import re
import traceback
import urllib.parse

SECRET_RE = re.compile(r"(SECRET|PASSWORD|API_KEY|TOKEN|PRIVATE)", re.I)
DANGER_SQL = re.compile(r"f[\"'].*(SELECT|INSERT|UPDATE|DELETE)", re.I)
DANGER_CMD = re.compile(r"(os\.system|subprocess\.(call|run|Popen)|eval\(|exec\()")
DANGER_PATH = re.compile(r"open\(\s*[A-Za-z_][\w.]*\s*[,)]")

STATIC_DOC = (
    "Static serving MUST confine reads to the static root. "
    "Use safe_join(root, user_path): on PathTraversalError respond "
    "403/404 without revealing the filesystem layout or tracebacks."
)

_CONCAT_RE = re.compile(r"['\"]\s*\+\s*[a-zA-Z_]")
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


class SecurityError(Exception):
    """Base class for security rejections."""


class PathTraversalError(SecurityError):
    """Raised when a user path escapes the static root."""


def escape(text):
    return _html.escape(str(text))


def escape_html(text):
    return _html.escape(str(text), quote=True)


def escape_attr(value):
    return _html.escape(str(value), quote=True)


def ssr(template, **context):
    """Render ``{{ name }}`` placeholders with ALL values escaped."""
    out = template
    for key, value in context.items():
        out = out.replace("{{ " + key + " }}", escape_html(value))
        out = out.replace("{{" + key + "}}", escape_html(value))
    return out


def safe_join(root, user_path):
    """Join ``user_path`` onto ``root``; reject escapes with PathTraversalError."""
    root_abs = os.path.abspath(root)
    joined = os.path.abspath(os.path.join(root_abs, user_path))
    if joined != root_abs and not joined.startswith(root_abs + os.sep):
        raise PathTraversalError(f"path escapes static root: {user_path!r}")
    return joined


def is_safe_redirect(target):
    """Allow only relative same-origin paths (no scheme, no //host)."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return False
    if "\\" in target:
        return False
    parsed = urllib.parse.urlparse(target)
    return not parsed.scheme and not parsed.netloc


def safe_next(next_param, default="/"):
    if next_param and is_safe_redirect(next_param):
        return next_param
    return default


def safe_error(exc):
    """Render a generic error page that never leaks tracebacks or secrets."""
    _ = traceback.format_exception(exc)
    return ("<html><body><h1>Something went wrong</h1>"
            "<p>Please try again later.</p></body></html>")


def _handler_source(route):
    handler = route.get("handler")
    if handler is not None and hasattr(handler, "__source__"):
        return handler.__source__
    return route.get("handler_source", "")


def scan(app):
    """Scan an app descriptor for secret-leak / xss-risk /
    missing-auth-on-mutating-rpc findings."""
    findings = []
    for route in app.get("routes", []):
        path = route.get("path", "?")
        src = _handler_source(route)
        lowered = src.lower()
        if ("os.environ" in src and any(
                k in lowered for k in ("secret", "password", "token",
                                       "api_key", "apikey", "credential"))):
            findings.append({
                "kind": "secret-leak",
                "path": path,
                "detail": "handler reads a secret from the environment; "
                          "ensure it never reaches client bundles/logs",
            })
        if ("+" in src and "<div" in src and _CONCAT_RE.search(src)
                and "escape" not in lowered):
            findings.append({
                "kind": "xss-risk",
                "path": path,
                "detail": "handler concatenates HTML without escaping; "
                          "use security.ssr/escape_html",
            })
        methods = {m.upper() for m in route.get("methods", ["GET"])}
        if methods & _MUTATING and not route.get("auth", False):
            findings.append({
                "kind": "missing-auth-on-mutating-rpc",
                "path": path,
                "detail": f"mutating methods {sorted(methods & _MUTATING)} "
                          "without auth guard",
            })
    return findings


def check_source(source, filename="<pyweb>"):
    """Return a list of {'kind', 'line', 'message'} findings."""
    findings = []
    for i, line in enumerate(source.splitlines(), start=1):
        if DANGER_SQL.search(line):
            findings.append({"kind": "sql-injection", "line": i,
                             "message": "possible SQL string interpolation; use parameterized queries"})
        if DANGER_CMD.search(line):
            findings.append({"kind": "command-injection", "line": i,
                             "message": "dangerous call (os.system/subprocess/eval/exec)"})
    try:
        from .compiler.parser import parse_source
        tree = parse_source(source, filename=filename)[0]     # the Python of a .pyweb file, markup aside
    except (SyntaxError, ValueError):
        try:
            tree = ast.parse(source, filename=filename)
        except SyntaxError:
            return findings
    findings += _app_findings(tree, {f["line"] for f in findings if f["kind"] == "sql-injection"})
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decos = [ast.unparse(d) if hasattr(ast, "unparse") else "" for d in node.decorator_list]
            if any("browser" in d for d in decos):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and SECRET_RE.search(sub.id):
                        findings.append({"kind": "secret-leak", "line": getattr(sub, "lineno", 0),
                                         "message": f"server secret {sub.id!r} referenced from @browser code"})
    return findings


_SQL_CALLS = {"execute", "executemany", "run_sql", "raw", "sql", "fetch", "query_raw"}


def _built_string(node):
    """An expression that builds text from pieces (f-string, +, %, .format): SQL injection if it's a query."""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return True
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format"


def _decorator_calls(fn):
    for d in fn.decorator_list:
        call = d if isinstance(d, ast.Call) else None
        target = d.func if call else d
        name = ast.unparse(target)
        yield name, ({k.arg: k.value for k in call.keywords} if call else {})


def _app_findings(tree, already):
    """Checks that need the app's structure: SQL built from strings, pages that assume a signed-in
    visitor without asking for one, and Models owned by a user with no row policy."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _SQL_CALLS \
                and node.args and _built_string(node.args[0]) and node.lineno not in already:
            out.append({"kind": "sql-injection", "line": node.lineno,
                        "message": f"SQL built from strings passed to .{node.func.attr}(): pass values as "
                                   "parameters (?, %s) or use the query builder"})
    for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        decos = list(_decorator_calls(fn))
        page = next((kw for name, kw in decos if name.endswith((".page", ".layout")) or name in ("page", "layout")), None)
        if page is None or any(k in page and not (isinstance(page[k], ast.Constant) and not page[k].value)
                               for k in ("login", "roles")):
            continue
        for sub in ast.walk(fn):
            # auth.user().name: None for visitors who aren't signed in
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Call) \
                    and ast.unparse(sub.value.func).endswith(("auth.user", "session.user")):
                out.append({"kind": "page-needs-login", "line": sub.lineno,
                            "message": f"{fn.name}() reads {ast.unparse(sub)} but visitors who aren't signed "
                                       "in have no user (an error page): add login=True to the page"})
                break
    models, users = {}, {"User"}
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        if any(getattr(b, "id", getattr(b, "attr", "")) == "Model" for b in cls.bases):
            owned = [i.target.id for i in cls.body if isinstance(i, ast.AnnAssign) and isinstance(i.target, ast.Name)
                     and ast.unparse(i.annotation).replace(" | None", "") in users
                     and i.target.id in ("owner", "user", "author", "created_by", "account")]
            if owned:
                models[cls.name] = (cls.lineno, owned[0])
    policies = {ast.unparse(n.func.value) for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "policy"}
    for name, (line, field) in models.items():
        if name not in policies:
            out.append({"kind": "missing-policy", "line": line,
                        "message": f"{name} belongs to a user ({field}) but has no row policy: anyone who can "
                                   f"guess an id can read or change another user's rows. Add {name}.policy(...)"})
    return out


def assert_browser_safe(source, filename="<pyweb>"):
    leaks = [f for f in check_source(source, filename) if f["kind"] == "secret-leak"]
    if leaks:
        f = leaks[0]
        raise ValueError(f"ERROR: server secret referenced from browser-executed code ({filename}:{f['line']})")
    return True
