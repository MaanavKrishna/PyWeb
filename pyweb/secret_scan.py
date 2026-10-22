"""Find credentials written into source files (``pyweb check`` runs this).

Two kinds of finding:

* ``hardcoded-secret``: text in a known credential format (AWS, GitHub,
  Stripe, OpenAI, Anthropic, Slack and Google keys, private keys). These fail
  ``pyweb check``: a key in source ends up in git history, in builds and in
  every copy of the repository. Move it to an environment variable and
  revoke the old one.
* ``possible-secret``: a long, random-looking string assigned to a name like
  ``api_key`` or ``password``. A warning (``--strict`` fails on it).

A line ending in ``# pyweb: allow-secret`` is skipped (for test fixtures).
Found values are never printed in full.
"""

from __future__ import annotations

import math
import os
import re

PATTERNS = [
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-|svcacct-)?(?!ant-)[A-Za-z0-9_-]{32,}")),
    ("Stripe key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b")),
    ("Stripe webhook secret", re.compile(r"\bwhsec_[A-Za-z0-9]{24,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")),
]
SECRET_NAME = re.compile(r"(?i)(secret|passw(?:or)?d|api_?key|access_?key|token|private_?key|credential|auth_?key)")
ASSIGN = re.compile(r"""(?P<name>[A-Za-z_][\w.]*)\s*(?::[^=]+)?=\s*(?P<q>["'])(?P<value>[^"'\s]{16,})(?P=q)""")
PLACEHOLDER = re.compile(r"(?i)(example|changeme|change-me|your[-_]|xxxx|placeholder|dummy|test|fake|<|\$\{|\{\{)")
ALLOW = "pyweb: allow-secret"
EXTENSIONS = (".pyweb", ".py", ".toml", ".yaml", ".yml", ".json", ".cfg", ".ini", ".js", ".ts")


def entropy(text):
    """Shannon entropy in bits per character."""
    if not text:
        return 0.0
    counts = {c: text.count(c) for c in set(text)}
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def _mask(value):
    return value[:4] + "…" if len(value) > 8 else "…"


def scan_text(text, filename="<source>"):
    """Findings ``{"kind", "line", "message", "file"}`` for ``text``."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW in line:
            continue
        hit = False
        for label, pattern in PATTERNS:
            m = pattern.search(line)
            if m:
                out.append({"kind": "hardcoded-secret", "line": n, "file": filename,
                            "message": f"{label} in source ({_mask(m.group(0))}): move it to an environment "
                                       "variable and revoke this one (it is in your git history)"})
                hit = True
                break
        if hit:
            continue
        for m in ASSIGN.finditer(line):
            name, value = m.group("name"), m.group("value")
            if not SECRET_NAME.search(name.rsplit(".", 1)[-1]) or PLACEHOLDER.search(value):
                continue
            if entropy(value) >= 3.5 and not value.startswith(("http://", "https://", "/")):
                out.append({"kind": "possible-secret", "line": n, "file": filename,
                            "message": f"{name} is set to what looks like a real credential ({_mask(value)}): "
                                       "read it from the environment instead (os.environ[...])"})
    return out


def scan_app(app_file):
    """Scan the app file and the source files next to it (not dependencies, builds or hidden folders)."""
    folder = os.path.dirname(os.path.abspath(app_file))
    paths = [os.path.abspath(app_file)]
    skip = {"node_modules", "dist", "build", "venv", ".venv", "__pycache__", "vendor", "migrations"}
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        if root.count(os.sep) - folder.count(os.sep) > 2:
            dirs[:] = []
        for f in files:
            path = os.path.join(root, f)
            if f.endswith(EXTENSIONS) and path not in paths and os.path.getsize(path) < 1_000_000:
                paths.append(path)
    out = []
    for path in paths[:500]:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        out += scan_text(text, os.path.relpath(path))
    return out
