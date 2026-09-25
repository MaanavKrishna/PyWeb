"""Parser: markup tokenizing, nesting, control flow, errors, placeholders."""

import ast

import pytest

from pyweb.compiler import parser as P


def test_simple_nesting():
    ui = P.parse_ui_block({1: "<main>", 2: "  <h1>Hi</h1>", 3: "</main>"})
    assert len(ui) == 1 and ui[0].tag == "main"
    assert ui[0].children[0].tag == "h1"


def test_self_closing_and_void():
    ui = P.parse_ui_block({1: "<div>", 2: '  <input bind={x} />', 3: '  <br>', 4: "</div>"})
    assert ui[0].children[0].tag == "input"
    assert ui[0].children[0].attrs["bind"] == ("expr", "x", 2)


def test_expr_and_text_split():
    ui = P.parse_ui_block({5: "<p>Hello {name}!</p>"})
    texts = [type(n).__name__ for n in ui[0].children]
    assert "ExprNode" in texts and "TextNode" in texts


def test_attr_expr_with_braces():
    ui = P.parse_ui_block({1: "<button onclick={increment}>", 2: "</button>"})
    assert ui[0].attrs["onclick"][1] == "increment"


def test_for_control_block():
    ui = P.parse_ui_block({1: "for todo in todos:", 2: "  <label>", 3: "    {todo}", 4: "  </label>"})
    assert type(ui[0]).__name__ == "ControlFor"
    assert ui[0].target == "todo" and ui[0].iterable == "todos"


def test_if_else_control():
    ui = P.parse_ui_block({1: "if admin:", 2: "  <b>A</b>", 3: "else:", 4: "  <i>B</i>"})
    assert type(ui[0]).__name__ == "ControlIf"
    assert len(ui[0].body) == 1 and len(ui[0].orelse) == 1


def test_mismatched_tag_errors():
    with pytest.raises(SyntaxError):
        P.parse_ui_block({1: "<div>", 2: "</span>"})


def test_placeholders_form_valid_python():
    src = "from pyweb import App\napp = App()\n@app.page('/')\ndef H():\n    x = 1\n    <h1>Hello {x}</h1>\n"
    py, ui = P.split_sources(src)
    ast.parse(py)
    assert ui


def test_content_line_not_python():
    src = "from pyweb import App\napp = App()\n@app.page('/')\ndef H():\n    <button>\n        Count: {n}\n    </button>\n"
    py, ui = P.split_sources(src)
    ast.parse(py)
    assert len(ui) == 3


def test_component_vs_html_tag():
    ui = P.parse_ui_block({1: "<UserCard user={u} />", 2: "<div />"})
    assert ui[0].is_component and not ui[1].is_component


def test_source_lines_preserved():
    ui = P.parse_ui_block({41: "<p>{x}</p>"})
    assert ui[0].line == 41 and ui[0].children[0].line == 41
