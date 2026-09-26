"""PyWeb security: XSS escaping, traversal, redirects, errors, scanner.

Expected server behavior for static files: serve ONLY paths that resolve
inside the configured static root. ``safe_join`` is the test helper /
reference implementation — any request whose normalized path escapes the
root MUST be rejected with ``PathTraversalError`` (HTTP 403/404 upstream,
never a file read outside the root).
"""
import html
import os
import re
import traceback
import urllib.parse

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


def escape_html(text: str) -> str:
    """Escape text for HTML text nodes and double-quoted attributes."""
    return html.escape(str(text), quote=True)


def escape_attr(value: str) -> str:
    """Escape a value for use inside a double-quoted HTML attribute."""
    return html.escape(str(value), quote=True)


def ssr(template: str, **context) -> str:
    """Render ``{{ name }}`` placeholders with ALL values escaped.

    Every dynamic value is escaped for both text and attribute contexts,
    so ``<script>`` / ``<img onerror>`` payloads can never break out.
    """
    out = template
    for key, value in context.items():
        out = out.replace("{{ " + key + " }}", escape_html(value))
        out = out.replace("{{" + key + "}}", escape_html(value))
    return out


def safe_join(root: str, user_path: str) -> str:
    """Join ``user_path`` onto ``root``; reject escapes with PathTraversalError."""
    root_abs = os.path.abspath(root)
    joined = os.path.abspath(os.path.join(root_abs, user_path))
    if joined != root_abs and not joined.startswith(root_abs + os.sep):
        raise PathTraversalError(f"path escapes static root: {user_path!r}")
    return joined


def is_safe_redirect(target: str) -> bool:
    """Allow only relative same-origin paths (no scheme, no //host)."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return False
    if "\\" in target:
        return False
    parsed = urllib.parse.urlparse(target)
    return not parsed.scheme and not parsed.netloc


def safe_next(next_param: str | None, default: str = "/") -> str:
    """Return ``next_param`` if it is a safe same-origin path else default."""
    if next_param and is_safe_redirect(next_param):
        return next_param
    return default


def safe_error(exc: BaseException) -> str:
    """Render a generic error page that never leaks tracebacks or secrets."""
    _ = traceback.format_exception(exc)  # kept server-side only
    return ("<html><body><h1>Something went wrong</h1>"
            "<p>Please try again later.</p></body></html>")


def _handler_source(route: dict) -> str:
    handler = route.get("handler")
    if handler is not None and hasattr(handler, "__source__"):
        return handler.__source__
    return route.get("handler_source", "")


def scan(app: dict) -> list:
    """Scan an app descriptor for common security findings.

    ``app`` is a mapping with a ``routes`` list; each route may carry
    ``path``, ``methods``, ``auth`` and ``handler_source`` (or a
    ``handler`` with ``__source__``). Returns a list of finding dicts
    with ``kind`` (``secret-leak`` | ``xss-risk`` |
    ``missing-auth-on-mutating-rpc``), ``path`` and ``detail``.
    """
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
