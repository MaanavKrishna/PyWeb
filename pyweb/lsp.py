"""Language-server helpers: completions + diagnostics (Track D).

Dependency-free: exposes pure functions that an LSP server (or the
``pyweb`` CLI test harness) can call. Diagnostics map ``CompileError``
spans to LSP ``Diagnostic`` dicts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class CompletionItem:
    label: str
    kind: str  # Component | Property | File | Field | Keyword
    detail: str = ""
    insert_text: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "label": self.label,
            "kind": self.kind,
            "detail": self.detail,
            "insertText": self.insert_text or self.label,
        }


@dataclass
class DocumentState:
    """In-memory view of the project used for completions."""

    components: dict[str, list[str]] = field(default_factory=dict)  # name -> props
    routes: list[str] = field(default_factory=list)
    models: dict[str, list[str]] = field(default_factory=dict)  # model -> fields
    keywords: tuple[str, ...] = ("signal", "route", "rpc", "component")


def _prefix(items: Iterable[str], prefix: str) -> list[str]:
    return sorted(i for i in items if i.startswith(prefix))


def complete_components(state: DocumentState, prefix: str = "") -> list[CompletionItem]:
    """Complete component names."""
    return [
        CompletionItem(name, "Component", detail=f"props: {', '.join(state.components[name]) or '—'}")
        for name in _prefix(state.components, prefix)
    ]


def complete_props(state: DocumentState, component: str, prefix: str = "") -> list[CompletionItem]:
    """Complete prop names for *component*."""
    return [
        CompletionItem(p, "Property", detail=f"{component}.{p}")
        for p in _prefix(state.components.get(component, []), prefix)
    ]


def complete_routes(state: DocumentState, prefix: str = "") -> list[CompletionItem]:
    """Complete route paths."""
    return [CompletionItem(r, "File", detail="route") for r in _prefix(state.routes, prefix)]


def complete_model_fields(state: DocumentState, model: str, prefix: str = "") -> list[CompletionItem]:
    """Complete field names for *model*."""
    return [
        CompletionItem(f, "Field", detail=f"{model}.{f}")
        for f in _prefix(state.models.get(model, []), prefix)
    ]


def complete(state: DocumentState, prefix: str = "") -> list[CompletionItem]:
    """General completion: components, routes, models, and keywords."""
    items = complete_components(state, prefix)
    items += complete_routes(state, prefix)
    for model in _prefix(state.models, prefix):
        items.append(CompletionItem(model, "Field", detail=f"model: {', '.join(state.models[model])}"))
    items += [CompletionItem(k, "Keyword") for k in _prefix(state.keywords, prefix)]
    return items


def state_from_graph(graph: Any) -> DocumentState:
    """Build a :class:`DocumentState` from an :class:`AppGraph`-like object."""
    to_dict = getattr(graph, "to_dict", None)
    data = to_dict() if callable(to_dict) else (graph if isinstance(graph, dict) else {})
    components = {c["name"]: list(c.get("props", [])) for c in data.get("components", [])}
    routes = [r["path"] for r in data.get("routes", [])]
    models: dict[str, list[str]] = {}
    for m in data.get("models", []):
        if isinstance(m, dict):
            models[m.get("name", "?")] = list(m.get("fields", []))
    return DocumentState(components=components, routes=routes, models=models)


def diagnostics_for_compile_error(error: Any) -> list[dict[str, Any]]:
    """Map a ``CompileError`` (or list thereof) to LSP diagnostics.

    Accepted span shapes (all optional, degrade gracefully):

    * attributes ``file``/``path``, ``line`` (1-based), ``col``/``column``
      (0/1-based), ``end_line``, ``end_col``, ``message``/``msg``.
    * a ``span`` attribute that is a ``(line, col)`` tuple or a dict with
      the keys above.
    """
    errors = list(error) if isinstance(error, (list, tuple)) else [error]
    diags: list[dict[str, Any]] = []
    for err in errors:
        message = (
            getattr(err, "message", None)
            or getattr(err, "msg", None)
            or (err.get("message") if isinstance(err, dict) else None)
            or str(err)
        )
        span = getattr(err, "span", None) if not isinstance(err, dict) else None
        if isinstance(err, dict):
            inner = err.get("span")
            span = {**err, **inner} if isinstance(inner, dict) else err
        line = _int_or(getattr(span, "line", None) if span is not None and not isinstance(span, dict) else (span or {}).get("line"), 1)
        col = _int_or(getattr(span, "col", getattr(span, "column", None)) if span is not None and not isinstance(span, dict) else (span or {}).get("col", (span or {}).get("column")), 0)
        end_line = _int_or(getattr(span, "end_line", None) if span is not None and not isinstance(span, dict) else (span or {}).get("end_line"), line)
        end_col = _int_or(getattr(span, "end_col", getattr(span, "end_column", None)) if span is not None and not isinstance(span, dict) else (span or {}).get("end_col", (span or {}).get("end_column")), col + 1)
        source = getattr(err, "file", getattr(err, "path", None))
        if source is None and isinstance(span, dict):
            source = span.get("file", span.get("path"))
        diags.append(
            {
                "message": str(message),
                "severity": 1,  # Error
                "source": "pyweb",
                "range": {
                    "start": {"line": max(line - 1, 0), "character": max(col, 0)},
                    "end": {"line": max(end_line - 1, 0), "character": max(end_col, 0)},
                },
                **({"uri": str(source)} if source else {}),
            }
        )
    return diags


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
