"""Language-server helpers: symbols, completions, hover, boundary lens.

These are pure functions over source text + compiler output so any
LSP transport (e.g. pygls) can wrap them without importing server code.
"""

from __future__ import annotations

import ast
import re

from .compiler import parser as P

BROWSER_APIS = ["storage", "clipboard", "location", "camera", "notifications",
                "fetch", "websocket", "indexeddb", "geolocation", "canvas"]
DECORATORS = ["@app.page", "@server", "@browser", "@edge", "@worker",
              "@shared", "@component", "@task", "@cache", "@realtime",
              "@auth.required", "@permission"]
HTML_TAGS = ["main", "div", "span", "p", "h1", "h2", "article", "section",
             "button", "input", "form", "label", "ul", "li", "a", "img"]


def symbols(source):
    """Outline: pages, components, server fns, models, signals (best-effort)."""
    out = []
    try:
        tree = ast.parse(P.split_sources(source)[0])
    except SyntaxError:
        return out
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = [getattr(b, "id", "") for b in node.bases]
            kind = "model" if "Model" in bases else "class"
            out.append({"kind": kind, "name": node.name, "line": node.lineno})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decos = [ast.unparse(d) if hasattr(ast, "unparse") else "" for d in node.decorator_list]
            joined = " ".join(decos)
            if "page" in joined:
                kind = "page"
            elif any(k in joined for k in ("server", "worker", "edge", "browser", "task", "realtime")):
                kind = "rpc"
            elif "component" in joined:
                kind = "component"
            else:
                kind = "function"
            out.append({"kind": kind, "name": node.name, "line": node.lineno,
                        "decorators": decos})
    return out


def complete(source, line, col):
    """Prefix completions for the token ending at (line, col), 1-based line."""
    lines = source.splitlines()
    if not (1 <= line <= len(lines)):
        return []
    frag = lines[line - 1][:col]
    m = re.search(r"(@[\w.]*|[A-Za-z_][\w]*)$", frag)
    token = m.group(1) if m else ""
    pool = DECORATORS if token.startswith("@") else (
        BROWSER_APIS if "browser." in frag or "pyweb.browser" in frag else
        HTML_TAGS if "<" in frag else
        ["Signal", "Computed", "Resource", "Effect", "live", "App", "Model"])
    if token.startswith("@"):
        base = token[1:]
        out = []
        for c in pool:
            name = c[1:] if c.startswith("@") else c
            if name.startswith(base):
                out.append({"label": c, "insert": name[len(base):]})
        return out
    return [{"label": c, "insert": c[len(token):]} for c in pool
            if c.startswith(token)]


def hover(compiled, symbol):
    """Placement + reason lens for a signal/handler/rpc name."""
    for name, page in compiled.get("pages", {}).items():
        if symbol in page.get("placement", {}):
            loc, reason = page["placement"][symbol]
            return {"page": name, "symbol": symbol, "location": loc, "reason": reason}
    for spec in compiled.get("rpc", []):
        if spec["name"] == symbol:
            return {"symbol": symbol, "location": spec["location"],
                    "reason": f"typed RPC ({spec.get('line', '?')})"}
    return None


def boundary_lens(compiled):
    """Every symbol with its execution location for editor gutters."""
    lens = []
    for name, page in compiled.get("pages", {}).items():
        for sym, (loc, reason) in page.get("placement", {}).items():
            lens.append({"page": name, "symbol": sym, "location": loc, "reason": reason})
    return sorted(lens, key=lambda e: (e["page"], e["symbol"]))
