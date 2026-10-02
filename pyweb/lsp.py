"""Language-server helpers: symbols, completions, hover, boundary lens.

These are pure functions over source text + compiler output so any
LSP transport (e.g. pygls) can wrap them without importing server code.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

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


@dataclass
class CompletionItem:
    label: str
    kind: str = "Keyword"
    detail: str = ""
    insert_text: str = ""

    def to_dict(self):
        return {"label": self.label, "kind": self.kind,
                "detail": self.detail,
                "insertText": self.insert_text or self.label}


@dataclass
class DocumentState:
    components: dict = None
    routes: list = None
    models: dict = None
    keywords: tuple = ("signal", "route", "rpc", "component")

    def __post_init__(self):
        if self.components is None:
            self.components = {}
        if self.routes is None:
            self.routes = []
        if self.models is None:
            self.models = {}


def _prefix(items, prefix):
    return sorted(i for i in items if i.startswith(prefix))


def complete_components(state, prefix=""):
    return [CompletionItem(name, "Component",
                           detail=f"props: {', '.join(state.components[name]) or '—'}")
            for name in _prefix(state.components, prefix)]


def complete_props(state, component, prefix=""):
    return [CompletionItem(p, "Property", detail=f"{component}.{p}")
            for p in _prefix(state.components.get(component, []), prefix)]


def complete_routes(state, prefix=""):
    return [CompletionItem(r, "File", detail="route")
            for r in _prefix(state.routes, prefix)]


def complete_model_fields(state, model, prefix=""):
    return [CompletionItem(f, "Field", detail=f"{model}.{f}")
            for f in _prefix(state.models.get(model, []), prefix)]


def complete_state(state, prefix=""):
    items = complete_components(state, prefix)
    items += complete_routes(state, prefix)
    for model in _prefix(state.models, prefix):
        items.append(CompletionItem(model, "Field",
                                    detail=f"model: {', '.join(state.models[model])}"))
    items += [CompletionItem(k, "Keyword") for k in _prefix(state.keywords, prefix)]
    return items


def state_from_graph(graph):
    to_dict = getattr(graph, "to_dict", None)
    data = to_dict() if callable(to_dict) else (graph if isinstance(graph, dict) else {})
    components = {c["name"]: list(c.get("props", [])) for c in data.get("components", [])}
    routes = [r["path"] for r in data.get("routes", [])]
    models = {}
    for m in data.get("models", []):
        if isinstance(m, dict):
            models[m.get("name", "?")] = list(m.get("fields", []))
    return DocumentState(components=components, routes=routes, models=models)


def _int_or(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def diagnostics_for_compile_error(error):
    errors = list(error) if isinstance(error, (list, tuple)) else [error]
    diags = []
    for err in errors:
        message = (getattr(err, "message", None) or getattr(err, "msg", None)
                   or (err.get("message") if isinstance(err, dict) else None)
                   or str(err))
        span = getattr(err, "span", None) if not isinstance(err, dict) else None
        if isinstance(err, dict):
            inner = err.get("span")
            span = {**err, **inner} if isinstance(inner, dict) else err
        if span is not None and not isinstance(span, dict):
            line = _int_or(getattr(span, "line", None), 1)
            col = _int_or(getattr(span, "col", getattr(span, "column", None)), 0)
            end_line = _int_or(getattr(span, "end_line", None), line)
            end_col = _int_or(getattr(span, "end_col", getattr(span, "end_column", None)), col + 1)
        else:
            span = span or {}
            line = _int_or(span.get("line"), 1)
            col = _int_or(span.get("col", span.get("column")), 0)
            end_line = _int_or(span.get("end_line"), line)
            end_col = _int_or(span.get("end_col", span.get("end_column")), col + 1)
        source = getattr(err, "file", getattr(err, "path", None))
        if source is None and isinstance(span, dict):
            source = span.get("file", span.get("path"))
        diags.append({
            "message": str(message),
            "severity": 1,
            "source": "pyweb",
            "range": {"start": {"line": max(line - 1, 0), "character": max(col, 0)},
                      "end": {"line": max(end_line - 1, 0), "character": max(end_col, 0)}},
            **({"uri": str(source)} if source else {}),
        })
    return diags
