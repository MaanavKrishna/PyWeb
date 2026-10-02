"""Parser v1: scanner, multi-line markup, stable line numbers, errors."""

import ast

import pytest

from pyweb.compiler import parser as P
from pyweb.compiler.ast import ControlFor, ControlIf, ExprNode


def page(body):
    head = "from pyweb import App\napp = App()\n\n@app.page('/')\ndef Home():\n"
    return head + "\n".join("    " + l if l else "" for l in body.splitlines()) + "\n"


def ui_of(body):
    tree, ui, pages = P.parse_source(page(body))
    return ui


def test_python_source_keeps_line_count():
    src = page("items = [1, 2]\n<ul>\n    for i in items:\n        <li>{i}</li>\n</ul>\nx = 1")
    py, _ = P.split_sources(src)
    assert len(py.splitlines()) == len(src.splitlines())
    ast.parse(py)


def test_line_numbers_after_control_blocks_are_real():
    src = page("items = []\n<ul>\n    for i in items:\n        <li>{i}</li>\n</ul>") + \
        "\n\ndef after():\n    return undefined_name\n"
    tree, _ui, _ = P.parse_source(src)
    fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "after"][0]
    assert src.splitlines()[fn.lineno - 1].startswith("def after")
    assert ast.get_source_segment(src, fn).startswith("def after")


def test_nested_braces_in_expression():
    ui = ui_of('<p>{len({"x": 1})} and {d["a"]}</p>')
    exprs = [c.code for c in ui[0].children if isinstance(c, ExprNode)]
    assert exprs == ['len({"x": 1})', 'd["a"]']


def test_braces_inside_strings_are_ignored():
    ui = ui_of("<p>{'}' + f\"{x}\"}</p>")
    assert ui[0].children[0].code == "'}' + f\"{x}\""


def test_multiline_tag():
    ui = ui_of('name = ""\n<input\n    bind={name}\n    placeholder="Your name"\n    type="text" />')
    el = ui[0]
    assert el.tag == "input" and el.attrs["placeholder"][1] == "Your name"
    assert el.attrs["bind"][0] == "expr"


def test_multiline_expression_attribute():
    ui = ui_of("<button onclick={lambda: save(\n        1, 2)}>Go</button>")
    assert ui[0].attrs["onclick"][1].replace(" ", "") == "lambda:save(1,2)"


def test_gt_inside_attribute_expression():
    ui = ui_of("<button disabled={n > 3}>x</button>")
    assert ui[0].attrs["disabled"] == ("expr", "n > 3", ui[0].line)


def test_text_whitespace_preserved_within_line():
    ui = ui_of("<p>Hello {name}, welcome!</p>")
    kids = ui[0].children
    assert [type(k).__name__ for k in kids] == ["TextNode", "ExprNode", "TextNode"]
    assert kids[0].text == "Hello " and kids[2].text == ", welcome!"


def test_if_elif_else_chain():
    ui = ui_of("<div>\n    if n > 10:\n        <b>big</b>\n    elif n > 5:\n"
               "        <i>mid</i>\n    else:\n        <u>small</u>\n</div>")
    chain = ui[0].children[0]
    assert isinstance(chain, ControlIf) and chain.test == "n > 10"
    mid = chain.orelse[0]
    assert isinstance(mid, ControlIf) and mid.test == "n > 5"
    assert mid.body[0].tag == "i" and mid.orelse[0].tag == "u"


def test_if_with_text_body():
    ui = ui_of("<p>\n    if done:\n        All done!\n    else:\n        Keep going\n</p>")
    chain = ui[0].children[0]
    assert chain.body[0].text == "All done!" and chain.orelse[0].text == "Keep going"


def test_for_with_tuple_target():
    ui = ui_of("<ul>\n    for i, item in enumerate(items):\n        <li>{i}: {item}</li>\n</ul>")
    loop = ui[0].children[0]
    assert isinstance(loop, ControlFor) and loop.target == "i, item"


def test_html_comment_skipped():
    ui = ui_of("<div>\n    <!-- note -->\n    <p>x</p>\n</div>")
    assert [c.tag for c in ui[0].children] == ["p"]


def test_markup_after_handler_is_not_inside_handler():
    src = page("count = 0\n\ndef inc():\n    count += 1\n\n<button onclick={inc}>{count}</button>")
    py, _ = P.split_sources(src)
    tree = ast.parse(py)
    home = [n for n in tree.body if isinstance(n, ast.FunctionDef)][0]
    inc = [n for n in home.body if isinstance(n, ast.FunctionDef)][0]
    assert all("__pyweb_ui__" not in ast.unparse(s) for s in inc.body)


def test_unclosed_tag_reports_line():
    with pytest.raises(P.PyWebSyntaxError) as e:
        P.parse_source(page("<div>\n    <p>x</p>\n<span>y</span></div>\n</section>"))
    assert e.value.lineno is not None


def test_mismatched_close_reports_expected():
    with pytest.raises(P.PyWebSyntaxError) as e:
        P.parse_ui_block({3: "<div>", 4: "</span>"})
    assert "expected </div>" in str(e.value) and e.value.lineno == 4


def test_invalid_expression_is_an_error():
    with pytest.raises(P.PyWebSyntaxError):
        P.parse_ui_block({1: "<p>{a +}</p>"})


def test_unterminated_tag_at_eof():
    with pytest.raises(P.PyWebSyntaxError):
        P.parse_source(page("<input value={x}"))


def test_duplicate_attribute_rejected():
    with pytest.raises(P.PyWebSyntaxError):
        P.parse_ui_block({1: '<a href="x" href="y">t</a>'})


def test_comparison_in_python_is_not_markup():
    src = page("a = 1\nb = 2\nsmall = a <b\n<p>{small}</p>")
    py, ui = P.split_sources(src)
    assert "small = a <b" in py
    assert len(ui) == 1
