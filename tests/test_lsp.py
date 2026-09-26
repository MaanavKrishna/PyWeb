"""Tests for LSP completions and CompileError diagnostics (Track D)."""
from __future__ import annotations

from types import SimpleNamespace

from pyweb import lsp
from pyweb.lsp import (
    DocumentState,
    complete,
    complete_components,
    complete_model_fields,
    complete_props,
    complete_routes,
    diagnostics_for_compile_error,
    state_from_graph,
)


def _state() -> DocumentState:
    return DocumentState(
        components={"Card": ["title", "count"], "Shell": []},
        routes=["/", "/about"],
        models={"User": ["id", "email"]},
    )


def test_complete_component_names():
    labels = [i.label for i in complete_components(_state(), "Ca")]
    assert labels == ["Card"]


def test_complete_props():
    labels = [i.label for i in complete_props(_state(), "Card", "ti")]
    assert labels == ["title"]
    assert complete_props(_state(), "Missing") == []


def test_complete_routes():
    labels = [i.label for i in complete_routes(_state(), "/a")]
    assert labels == ["/about"]


def test_complete_model_fields():
    labels = [i.label for i in complete_model_fields(_state(), "User", "e")]
    assert labels == ["email"]


def test_complete_general_includes_keywords():
    labels = [i.label for i in complete(_state(), "sig")]
    assert "signal" in labels


def test_state_from_graph_dict():
    state = state_from_graph(
        {"components": [{"name": "Card", "props": ["title"]}],
         "routes": [{"path": "/"}], "models": [{"name": "User", "fields": ["id"]}]})
    assert state.components == {"Card": ["title"]}
    assert state.routes == ["/"]
    assert state.models == {"User": ["id"]}


def test_diagnostics_from_object_span():
    err = SimpleNamespace(message="bad prop", file="app.py",
                          span=SimpleNamespace(line=3, col=4, end_line=3, end_col=9))
    (diag,) = diagnostics_for_compile_error(err)
    assert diag["message"] == "bad prop" and diag["severity"] == 1
    assert diag["range"]["start"] == {"line": 2, "character": 4}
    assert diag["range"]["end"] == {"line": 2, "character": 9}
    assert diag["uri"] == "app.py"


def test_diagnostics_from_dict_and_list():
    diags = diagnostics_for_compile_error(
        [{"message": "oops", "line": 1}, {"msg": "again", "span": {"line": 2, "col": 1}}])
    assert len(diags) == 2 and diags[0]["message"] == "oops"
    assert diags[1]["range"]["start"]["line"] == 1


def test_diagnostics_degrade_gracefully():
    (diag,) = diagnostics_for_compile_error("kaboom")
    assert diag["message"] == "kaboom" and diag["severity"] == 1
    assert lsp.CompletionItem("x", "Component").to_dict()["label"] == "x"
