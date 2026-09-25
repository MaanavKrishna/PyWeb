"""LSP helpers: outline symbols, completions, hover, boundary lens."""

from pyweb import lsp
from pyweb.compiler import compile_source


SRC = """from pyweb import App, server
from pyweb import Model

app = App()

class Post(Model):
    title: str

@server
def latest() -> str:
    return 'x'

@app.page("/")
def Home():
    count = 0
    def inc():
        count += 1
    <button onclick={inc}>{count}</button>
"""


def test_symbols_outline():
    syms = lsp.symbols(SRC)
    by_name = {s["name"]: s for s in syms}
    assert by_name["Post"]["kind"] == "model"
    assert by_name["latest"]["kind"] == "rpc"
    assert by_name["Home"]["kind"] == "page"


def test_symbols_broken_source_empty():
    assert lsp.symbols("def broken(:") == []


def test_complete_decorators():
    labels = [c["label"] for c in lsp.complete("@app.pa", 1, 7)]
    assert "@app.page" in labels
    assert lsp.complete("@app.pa", 1, 7)[0]["insert"] == "ge"


def test_complete_browser_apis():
    labels = [c["label"] for c in lsp.complete("from pyweb.browser import storage\nbrowser.sto", 2, 11)]
    assert "storage" in labels


def test_complete_html_after_bracket():
    labels = [c["label"] for c in lsp.complete("<bu", 1, 3)]
    assert "button" in labels


def test_complete_out_of_range():
    assert lsp.complete("x", 99, 1) == []


def test_hover_signal_shows_placement():
    compiled = compile_source(SRC)
    h = lsp.hover(compiled, "count")
    assert h["location"] == "browser" and "reason" in h


def test_hover_rpc():
    compiled = compile_source(SRC)
    h = lsp.hover(compiled, "latest")
    assert h["location"] == "server"


def test_hover_unknown_none():
    compiled = compile_source(SRC)
    assert lsp.hover(compiled, "nope") is None


def test_boundary_lens_sorted():
    compiled = compile_source(SRC)
    lens = lsp.boundary_lens(compiled)
    assert any(e["symbol"] == "count" for e in lens)
    assert lens == sorted(lens, key=lambda e: (e["page"], e["symbol"]))
