"""Track A: typed props validation, children/slots, CompileError spans."""
from __future__ import annotations

import pytest

from pyweb.compiler.ast import CompileError
from pyweb.compiler.pipeline import build_text


def test_missing_required_prop_is_compile_error_with_span():
    src = """from pyweb import signal

@component(props={"title": str})
def Card():
    <div>{title}</div>

@page("/")
def home():
    <div>
        <Card />
    </div>
"""
    with pytest.raises(CompileError) as ei:
        build_text(src, filename="app.pyweb")
    err = ei.value
    assert "title" in str(err)
    assert "missing" in str(err).lower()
    s = err.span
    assert s.file == "app.pyweb"
    assert s.start_line > 0 and s.start_col >= 0
    assert s.snippet, "error must carry a source snippet"
    assert err.hint, "error must carry a hint"
    rendered = str(err)
    assert "app.pyweb" in rendered and str(s.start_line) in rendered


def test_extra_prop_is_compile_error():
    src = """from pyweb import signal

@component(props={"title": str})
def Card():
    <div>{title}</div>

@page("/")
def home():
    <div>
        <Card title="hi" bogus="x" />
    </div>
"""
    with pytest.raises(CompileError) as ei:
        build_text(src, filename="app.pyweb")
    assert "bogus" in str(ei.value)


def test_optional_prop_with_default_builds():
    src = """from pyweb import signal

@component(props={"title": str, "body": "untitled"})
def Card():
    <div>
        <h2>{title}</h2>
        <p>{body}</p>
    </div>

@page("/")
def home():
    <div>
        <Card title="hi" />
    </div>
"""
    art = build_text(src, filename="app.pyweb")
    assert "untitled" in art.html
    assert ">hi<" in art.html


def test_children_slot_renders_and_hydrates():
    src = """from pyweb import signal

@component(props={"title": str})
def Card():
    <div>
        <h2>{title}</h2>
        <slot />
    </div>

@page("/")
def home():
    <div>
        <Card title="t">
            <span>inner</span>
        </Card>
    </div>
"""
    art = build_text(src, filename="app.pyweb")
    assert ">t<" in art.html
    assert "<span>inner</span>" in art.html


def test_compile_error_format_has_file_line_col_snippet_hint():
    src = """from pyweb import signal

@page("/")
def home():
    x = signal(0)

    <div>
        <button onclick={nope}>x</button>
    </div>
"""
    with pytest.raises(CompileError) as ei:
        build_text(src, filename="pages/home.pyweb")
    err = ei.value
    text = str(err)
    assert "pages/home.pyweb" in text
    assert err.span.start_line == 8, text
    assert err.span.snippet.strip().startswith("<button"), text
    assert err.hint
    assert "^" in text  # caret line
