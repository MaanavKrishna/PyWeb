"""Editor support for server options, organizations and webhooks: completions for ``@server(...)``
and ``@app.page(...)`` keywords, ``org_role=``, ``per=``, ``rate=``, plan features, ``tenant=True``
and webhook schemes; warnings for a malformed ``rate=``, an unknown organization role, and a
``tenant=True`` Model read with ``.unscoped()`` outside ``with system():`` (which crosses
organizations)."""

from __future__ import annotations

import ast
import re

SERVER_KEYS = {"login": "require a signed-in user (401)", "roles": "require these roles (403)",
               "fresh": "require a sign-in in the last N seconds", "rate": 'calls allowed, e.g. "10/minute"',
               "per": 'count calls per "user", "ip", "org" or "all"',
               "org_role": "require this role in the current organization",
               "feature": "require a plan feature (auth.plans())"}
PAGE_KEYS = {"title": "the page title", "description": "meta description", "image": "social preview image",
             "canonical": "canonical URL", "noindex": "keep it out of search engines", "layout": "a layout, or None",
             "login": "send visitors who aren't signed in to /login", "roles": "require these roles (403)",
             "fresh": "require a sign-in in the last N seconds",
             "org_role": "require this role in the current organization",
             "feature": "require a plan feature (auth.plans())"}
ORG_ROLES = ("owner", "admin", "member", "viewer")
PER = ("user", "ip", "org", "all")
RATES = ("10/minute", "5/second", "100/hour", "1000/day", "30/5m")
SCHEMES = ("standard", "github", "stripe")


def _plan_features(text):
    """Feature names from ``auth.plans({...})`` in the file: keys whose values are True/False."""
    out = []
    for m in re.finditer(r"""["'](\w+)["']\s*:\s*(True|False)""", text):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def _open_call(prefix, names):
    """``(name, already used keywords)`` when the cursor is inside one of these calls' arguments."""
    depth, i = 0, len(prefix) - 1
    while i >= 0:
        c = prefix[i]
        if c in ")]}":
            depth += 1
        elif c in "([{":
            if depth == 0:
                if c != "(":
                    return None
                m = re.search(r"(?:@|\.)(\w+)\s*$", prefix[:i])
                if m and m.group(1) in names:
                    return m.group(1), set(re.findall(r"(\w+)\s*=", prefix[i:]))
                return None
            depth -= 1
        i -= 1
    return None


def completions(prefix, text):
    item = lambda label, kind=12, detail="": {"label": label, "kind": kind, "detail": detail}  # noqa: E731
    m = re.search(r"\b(org_role|per|rate|scheme|feature)\s*=\s*(?:\[[^\]]*)?[\"']([\w/-]*)$", prefix)
    if m:
        values = {"org_role": ORG_ROLES, "per": PER, "rate": RATES, "scheme": SCHEMES,
                  "feature": _plan_features(text)}[m.group(1)]
        return [item(v, 12, m.group(1)) for v in values]
    if re.search(r"^\s*class\s+\w+\([^)]*\bModel\b[^)]*,\s*\w*$", prefix):
        return [{"label": "tenant=True", "kind": 5, "detail": "rows belong to an organization",
                 "insertText": "tenant=True"}]
    call = _open_call(prefix, ("server", "page", "layout"))
    if call and re.search(r"(?:\(|,)\s*\w*$", prefix):
        keys = SERVER_KEYS if call[0] == "server" else PAGE_KEYS
        return [{"label": k, "kind": 5, "detail": d, "insertText": k + "="} for k, d in keys.items()
                if k not in call[1]]
    return None


def _tenant_models(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
                k.arg == "tenant" and isinstance(k.value, ast.Constant) and k.value.value is True
                for k in node.keywords):
            out.add(node.name)
    return out


def _base_name(node):
    while isinstance(node, (ast.Call, ast.Attribute)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def problems(text, tree):
    """``[(line, code, message)]``."""
    from .decorators import parse_rate
    if tree is None:
        return []
    out = []
    tenant = _tenant_models(tree)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def in_system(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.With) and any(
                    isinstance(i.context_expr, ast.Call) and getattr(i.context_expr.func, "id",
                                                                     getattr(i.context_expr.func, "attr", "")) == "system"
                    for i in node.items):
                return True
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        unscoped = isinstance(node.func, ast.Attribute) and node.func.attr == "unscoped"
        if unscoped and _base_name(node) in tenant and not in_system(node):
            out.append((node.lineno, "tenant-unscoped",
                        f"{_base_name(node)} is tenant=True: .unscoped() reads every organization's rows. "
                        "Wrap it in `with system():` if that's what you mean"))
        fn = getattr(node.func, "id", getattr(node.func, "attr", None))
        if fn not in ("server", "page", "layout"):
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        rate = kw.get("rate")
        if isinstance(rate, ast.Constant) and isinstance(rate.value, str):
            per = kw.get("per")
            try:
                parse_rate(rate.value, per.value if isinstance(per, ast.Constant) else "user")
            except ValueError as exc:
                out.append((node.lineno, "bad-rate", str(exc)))
        role = kw.get("org_role")
        roles = [role] if isinstance(role, ast.Constant) else (role.elts if isinstance(role, (ast.List, ast.Tuple)) else [])
        for r in roles:
            if isinstance(r, ast.Constant) and r.value not in ORG_ROLES:
                out.append((node.lineno, "unknown-org-role",
                            f"org_role {r.value!r} isn't a role ({', '.join(ORG_ROLES)})"))
    return out
