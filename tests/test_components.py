"""Components: real HTML output, escaping, void elements, lists."""

from pyweb.components import Article, Button, Form, Heading, Input, Node, Page, el


def test_escape_text():
    assert el("p", "<b>&").render() == "<p>&lt;b&gt;&amp;</p>"


def test_attr_escape_and_bool():
    assert 'title="a&quot;b"' in el("div", "x", title='a"b').render()
    assert "<input disabled>" in el("input", disabled=True).render()
    assert "hidden" not in el("div", "x", hidden=False).render()


def test_void_elements():
    assert el("br").render() == "<br>"
    assert el("input", type="text").render() == '<input type="text">'


def test_nested_lists_flatten():
    assert el("ul", [el("li", "a"), el("li", "b")]).render() == "<ul><li>a</li><li>b</li></ul>"


def test_helpers():
    assert Heading("Hi").render() == "<h1>Hi</h1>"
    assert Heading("Hi", level=3).render() == "<h3>Hi</h3>"
    assert Button("Go").render() == "<button>Go</button>"
    assert Input(type="email").render() == '<input type="email">'
    assert "<form" in Form("x").render() and "<article>" in Article("y").render()


def test_class_underscore_and_reactive_skipped():
    assert 'class="c"' in el("div", "x", class_="c").render()
    assert "onclick" not in el("button", "x", onclick="f").render()
    assert "bind" not in el("input", bind="q").render()


def test_page_wrapper():
    assert 'data-page="T"' in Page("x", title="T").render()


def test_tag_lowercased():
    assert Node("DIV", ["x"], {}).render() == "<div>x</div>"
