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
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return findings
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decos = [ast.unparse(d) if hasattr(ast, "unparse") else "" for d in node.decorator_list]
            if any("browser" in d for d in decos):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and SECRET_RE.search(sub.id):
                        findings.append({"kind": "secret-leak", "line": getattr(sub, "lineno", 0),
                                         "message": f"server secret {sub.id!r} referenced from @browser code"})
    return findings


def assert_browser_safe(source, filename="<pyweb>"):
    leaks = [f for f in check_source(source, filename) if f["kind"] == "secret-leak"]
    if leaks:
        f = leaks[0]
        raise ValueError(f"ERROR: server secret referenced from browser-executed code ({filename}:{f['line']})")
    return True
