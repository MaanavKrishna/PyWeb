"""Track E port: every executable docs snippet runs green.

Each ``examples/snippets/*.py`` module is the tested source behind the
docs guides (docs/00-07). Importing + exercising them here guarantees
docs code fences never rot.
"""

from __future__ import annotations

import importlib
import runpy
from pathlib import Path

import pytest

SNIPPETS = sorted(
    p.stem for p in (Path(__file__).parent.parent / "examples" / "snippets").glob("*.py")
    if p.stem != "__init__"
)


@pytest.mark.parametrize("name", SNIPPETS)
def test_snippet_imports(name):
    mod = importlib.import_module(f"examples.snippets.{name}")
    assert mod.__doc__, name


def test_hello_counter_renders():
    from examples.snippets.hello_counter import render
    html = render(3)
    assert 'pw-bind="count"' in html and ">3<" in html


def test_todo_crud_roundtrip():
    from examples.snippets import todo_crud as t
    todo = t.add_todo("write docs")
    assert todo["title"] == "write docs"
    assert t.toggle_todo(todo["id"])["done"] is True
    t.delete_todo(todo["id"])
    with pytest.raises(ValueError):
        t.add_todo("   ")
    with pytest.raises(KeyError):
        t.toggle_todo("missing")


def test_e2e_workflow_steps():
    from examples.snippets.e2e_usage import workflow
    steps = workflow()
    assert "compile .pyweb" in steps and len(steps) >= 5


def test_reactivity_snippet_executes():
    import examples.snippets.reactivity as r
    assert r is not None


def test_offline_sync_queues_and_replays():
    import examples.snippets.offline_sync as o
    assert o is not None


def test_all_snippets_run_as_scripts():
    root = Path(__file__).parent.parent / "examples" / "snippets"
    for name in SNIPPETS:
        runpy.run_path(str(root / f"{name}.py"), run_name="__main__")
