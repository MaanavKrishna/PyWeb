"""0.6.1: errors show the line with a caret, suggest the name you meant, and link the docs."""

import subprocess
import sys

import pytest

from pyweb.compiler import compile_source
from pyweb.compiler.errors import (CompileError, describe, did_you_mean, explain_exception, frame, pretty,
                                   suggest)

BASE = '''from pyweb import App, server
from pyweb.ui import Button
app = App(title="t")

def Greeting(name: str):
    <p>Hello {name}</p>

@server
def save(text: str) -> str:
    return text

@app.page("/")
def Home():
    count = 0
    <main>
        <h1>{count}</h1>
        CASE
    </main>
'''


def app_with(line):
    return BASE.replace("CASE", line)


def compile_error(line):
    with pytest.raises((CompileError, SyntaxError)) as info:
        compile_source(app_with(line), filename="app.pyweb")
    return info.value


def test_suggest_picks_the_close_name_and_nothing_when_far():
    assert suggest("nme", ["name", "id"]) == "name"
    assert suggest("NAME", ["name"]) == "name"
    assert suggest("zzz", ["name", "count"]) is None
    assert did_you_mean("cuont", ["count"]) == "; did you mean 'count'?"
    assert did_you_mean("x", []) == ""


@pytest.mark.parametrize("line, expect", [
    ('<Greeting nme="x" />', "<Greeting> has no prop 'nme'; did you mean 'name'?"),
    ('<Greetng name="x" />', "unknown component <Greetng>; did you mean <Greeting>?"),
    ('<button onclick={lambda: cuont + 1}>+</button>', "name 'cuont' is not defined in browser code; did you mean 'count'?"),
    ('<button onclick={lambda: save(txt="x")}>s</button>', "save() got an unexpected keyword argument 'txt'; did you mean 'text'?"),
])
def test_compile_errors_suggest_the_name_you_meant(line, expect):
    assert expect in str(compile_error(line))


def test_no_suggestion_when_nothing_is_close():
    err = compile_error('<Greeting colour="x" />')
    assert "did you mean" not in str(err) and "props: name" in str(err)


def test_str_format_is_unchanged_for_editors_and_ci():
    err = compile_error('<button onclick={lambda: cuont + 1}>+</button>')
    assert str(err).startswith("app.pyweb:17: name 'cuont'")


def test_frame_points_at_the_line_and_column():
    src = "a\nb = 1\n<p>{cuont}</p>\nd"
    assert frame(src, 3, 4) == "  2 | b = 1\n> 3 | <p>{cuont}</p>\n    |     ^\n  4 | d"
    assert frame(src, 99) == "" and frame("", 1) == ""


def test_describe_and_pretty_have_hint_docs_and_caret():
    err = compile_error('<button onclick={lambda: cuont + 1}>+</button>')
    rec = describe(err, app_with('<button onclick={lambda: cuont + 1}>+</button>'))
    assert rec["line"] == 17 and rec["file"] == "app.pyweb"
    assert rec["docs"].endswith("/browser-python.html") and "page variable" in rec["hint"]
    caret_line = rec["frame"].splitlines()[2]
    assert caret_line.index("^") - caret_line.index("|") - 2 == rec["col"] == app_with("").splitlines()[16].find("") + \
        "        <button onclick={lambda: cuont + 1}>+</button>".find("cuont")
    text = pretty(err, app_with('<button onclick={lambda: cuont + 1}>+</button>'))
    assert text.splitlines()[0].startswith("app.pyweb:17: ") and "hint: " in text and "docs: " in text


def test_check_prints_location_once_with_frame_hint_and_docs(tmp_path):
    (tmp_path / "app.pyweb").write_text(app_with('<Greeting nme="x" />'))
    res = subprocess.run([sys.executable, "-m", "pyweb.cli", "check", "app.pyweb"], cwd=tmp_path,
                         capture_output=True, text=True)
    assert res.returncode == 1
    first = res.stdout.splitlines()[0]
    assert first == "compile-error app.pyweb:17: <Greeting> has no prop 'nme'; did you mean 'name'?"
    assert "> 17 |" in res.stdout and "^" in res.stdout and "docs: https://" in res.stdout


def test_build_shows_the_frame_on_a_compile_error(tmp_path):
    (tmp_path / "app.pyweb").write_text(app_with('<Greetng name="x" />'))
    res = subprocess.run([sys.executable, "-m", "pyweb.cli", "build", "app.pyweb", "--out", "dist"], cwd=tmp_path,
                         capture_output=True, text=True)
    assert res.returncode == 1 and "did you mean <Greeting>?" in res.stderr and "> 17 |" in res.stderr


def test_check_reports_unknown_kit_variants_with_a_suggestion(tmp_path):
    (tmp_path / "app.pyweb").write_text(app_with('<Button variant="outlin">x</Button>'))
    res = subprocess.run([sys.executable, "-m", "pyweb.cli", "check", "app.pyweb"], cwd=tmp_path,
                         capture_output=True, text=True)
    assert res.returncode == 0                     # a warning, not an error
    assert "unknown-variant app.pyweb:17: <Button> has no variant 'outlin' (did you mean 'outline'?" in res.stdout


def test_components_on_the_button_base_accept_the_default_variant():
    from pyweb.ui.extending import values
    assert "default" in values("DialogClose")["variant"]


def test_overlay_shows_frame_and_escapes(tmp_path):
    from pyweb.cli import _error_overlay
    path = tmp_path / "app.pyweb"
    path.write_text(app_with('<Greeting nme="<x>" />'))
    try:
        compile_source(path.read_text(), filename=str(path))
    except CompileError as exc:
        page = _error_overlay(exc, str(path))
    assert "did you mean &#x27;name&#x27;?" in page and "&lt;x&gt;" in page and "<x>" not in page
    assert "class=code" in page and "Hint:" in page and "Read the docs" in page


def test_runtime_errors_lead_with_the_apps_own_frames(tmp_path):
    import html
    import re
    from pyweb.testing import TestClient
    path = tmp_path / "app.pyweb"
    path.write_text('from pyweb import App\napp = App()\n\ndef total(items):\n    return sum(items) / len(items)\n\n'
                    '@app.page("/")\ndef Home():\n    avg = total([])\n    <p>{avg}</p>\n')
    res = TestClient(str(path)).get("/")
    assert res.status == 500
    text = html.unescape(re.search(r"<pre[^>]*>(.*?)</pre>", res.text, re.S).group(1))
    lines = text.splitlines()
    assert lines[0] == "ZeroDivisionError: division by zero"
    assert lines[2] == "In your code (most recent call first):"
    assert lines[3].endswith("app.pyweb:5, in total") and "return sum(items) / len(items)" in lines[5]
    assert any(line.endswith("app.pyweb:9, in Home") for line in lines) and "Full traceback:" in text


def test_explain_exception_without_app_frames_is_the_plain_traceback():
    try:
        {}["missing"]
    except KeyError as exc:
        assert explain_exception(exc).startswith("KeyError: 'missing'")


def test_mcp_check_returns_frame_and_docs(tmp_path, monkeypatch):
    from pyweb import mcp
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.pyweb").write_text(app_with('<Greeting nme="x" />'))
    res = mcp.Server().call_tool("pyweb_check", {"path": "app.pyweb"}, None)
    import json
    data = res.get("structuredContent") or json.loads(res["content"][0]["text"])
    err = data["errors"][0]
    assert err["line"] == 17 and "did you mean 'name'" in err["message"]
    assert "> 17 |" in err["frame"] and err["docs"].startswith("https://") and err["hint"]
