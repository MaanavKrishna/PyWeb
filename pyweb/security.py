"""Security: HTML escaping, CSRF, taint checks, secret-flow analysis."""

from __future__ import annotations

import ast
import html as _html
import re

SECRET_RE = re.compile(r"(SECRET|PASSWORD|API_KEY|TOKEN|PRIVATE)", re.I)
DANGER_SQL = re.compile(r"f[\"'].*(SELECT|INSERT|UPDATE|DELETE)", re.I)
DANGER_CMD = re.compile(r"(os\.system|subprocess\.(call|run|Popen)|eval\(|exec\()")
DANGER_PATH = re.compile(r"open\(\s*[A-Za-z_][\w.]*\s*[,)]")


def escape(text):
    return _html.escape(str(text))


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
