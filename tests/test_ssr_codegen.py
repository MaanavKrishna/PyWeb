"""Compiled server rendering (ssr_codegen) matches the interpreter (ssr.Renderer) byte for byte."""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pyweb.compiler.ast import ControlFor, ControlIf, Element, ExprNode, TextNode
from pyweb.ssr import Renderer, RenderError

VALUES = st.one_of(
    st.none(), st.booleans(), st.integers(-5, 5),
    st.sampled_from(["", "a", "<b>&\"'", "javascript:alert(1)", " JaVaScRiPt:x", "/docs", "/docs/x", "#top",
                     "https://e.com/", "data:text/html,x"]),
    st.dictionaries(st.sampled_from(["on", "off", "color", "fontSize"]),
                    st.one_of(st.booleans(), st.none(), st.sampled_from(["red", "1px"])), max_size=3),
    st.lists(st.sampled_from(["x", "y", ""]), max_size=3),
)
NAMES = ["a", "b", "c"]
ATTRS = ["class", "class_", "style", "href", "src", "title", "id", "disabled", "data-x", "value", "onclick", "ref"]


def _attrs():
    lit = st.tuples(st.just("lit"), st.sampled_from(["", "x", "<&>", "javascript:1", "/docs"]), st.just(1))
    expr = st.tuples(st.just("expr"), st.sampled_from(NAMES + ["item", "a or b", "[a, b]"]), st.just(1))
    return st.dictionaries(st.sampled_from(ATTRS), st.one_of(st.just(True), lit, expr), max_size=4)


def _tree(depth):
    leaf = st.one_of(
        st.builds(TextNode, st.sampled_from(["hi", "<x>", "&amp;", "", " "])),
        st.builds(ExprNode, st.sampled_from(NAMES + ["item", "len(str(a))", "[x for x in (b or [])][:1]"])),
    )
    if depth == 0:
        return leaf
    kids = st.lists(_tree(depth - 1), max_size=3)
    return st.one_of(
        leaf,
        st.builds(Element, st.sampled_from(["div", "a", "input", "p", "img", "span"]), _attrs(), kids),
        st.builds(ControlIf, st.sampled_from(NAMES + ["not a"]), kids, kids),
        st.builds(ControlFor, st.sampled_from(["item", "item, _"]),
                  st.sampled_from(["[1, 2]", "[('x', 1), ('<', 2)]", "b if isinstance(b, list) else []"]), kids),
    )


def render_both(nodes, env, path="/docs"):
    out = []
    for compiled in (False, True):
        r = Renderer({}, strict=True, path=path, compiled=compiled)
        try:
            out.append(("ok", r.render(nodes, env)))
        except RenderError as exc:
            out.append(("error", exc.lineno))
        except Exception as exc:  # noqa: BLE001 - both must fail the same way
            out.append(("raised", type(exc).__name__))
    return out


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(_tree(3), max_size=4), st.fixed_dictionaries({n: VALUES for n in NAMES}))
def test_compiled_matches_interpreter(nodes, env):
    env = dict(env, item="page item")          # a page variable named like a loop variable
    interpreted, compiled = render_both(nodes, env)
    if interpreted[0] == "ok":
        assert compiled == interpreted
    else:
        assert compiled[0] != "ok" or interpreted[0] == "raised"


def test_loop_variables_stay_inside_their_loop():
    nodes = [ControlFor("item", "[1, 2]", [ExprNode("item")]), TextNode("|"), ExprNode("item")]
    assert render_both(nodes, {"item": "page"}) == [("ok", "12|page")] * 2


def test_errors_keep_the_expression_and_line():
    nodes = [Element("p", {}, [ExprNode("1 / zero", line=7)], 6)]
    r = Renderer({"zero": 0}, strict=True, compiled=True)
    with pytest.raises(RenderError) as err:
        r.render(nodes, {})
    assert err.value.lineno == 7 and "{1 / zero} raised ZeroDivisionError" in str(err.value)


def test_current_link_marks_follow_the_request_path():
    nodes = [Element("a", {"href": ("lit", "/docs", 1)}, [TextNode("Docs")], 1),
             Element("a", {"href": ("expr", "u", 1)}, [TextNode("X")], 1)]
    interpreted, compiled = render_both(nodes, {"u": "/docs/x"}, path="/docs/x")
    assert compiled == interpreted
    assert compiled[1] == '<a aria-current="true" href="/docs">Docs</a><a aria-current="page" href="/docs/x">X</a>'


def test_examples_render_the_same_both_ways(monkeypatch, tmp_path):
    """Every example's pages (that need no route data) give identical HTML."""
    import glob
    import os

    from pyweb import ssr
    from pyweb.testing import TestClient
    monkeypatch.setattr(ssr, "_VERIFY", True)        # each render is checked against the interpreter
    monkeypatch.setenv("PYWEB_WORKER", "0")
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples"))
    monkeypatch.chdir(tmp_path)                       # databases the examples create land here
    checked = 0
    for app in sorted(glob.glob(os.path.join(root, "*", "app.pyweb"))):
        if "saas" in app or "ai-chat" in app:
            continue                                  # need accounts / an AI provider
        client = TestClient(app)
        for page in client.app.compiled["pages"].values():
            route = page["route"]
            if not route or "{" in route or page.get("error_status"):
                continue
            assert client.get(route).status in (200, 303), (app, route)
            checked += 1
    assert checked >= 8
