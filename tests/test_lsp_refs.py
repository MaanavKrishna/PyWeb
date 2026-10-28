"""LSP: find references, rename, quick fixes."""

from pyweb import lsp_refs
from pyweb.lsp import LanguageServer, _uri

SRC = '''from pyweb import App, server

app = App()


@server
def add(n):
    return n + 1


def Card(title, children=None):
    <div class="card"><h2>{title}</h2>{children}</div>


@app.page("/")
def Home():
    count = 0
    items = ["count"]

    def inc():
        count = add(count)

    <Card title="count">
        <p>count is {count}</p>
        if count > 2:
            <p data-n={count + 1}>{add(count)}</p>
        <button onclick={inc}>+</button>
    </Card>


@app.page("/other")
def Other():
    count = 5
    <p>{count}</p>
'''


def at(text, needle, nth=0):
    lines = text.splitlines()
    seen = 0
    for i, line in enumerate(lines):
        j = line.find(needle)
        while j >= 0:
            if seen == nth:
                return i, j
            seen += 1
            j = line.find(needle, j + 1)
    raise AssertionError(needle)


def test_page_variable_references_stay_in_their_function():
    line, col = at(SRC, "count = 0")
    refs = lsp_refs.references(SRC, None, line, col)
    lines = SRC.splitlines()
    used = [(lines[ln], c) for _f, ln, c, _n in refs]
    assert all(SRC.splitlines()[ln][c:c + 5] == "count" for _f, ln, c, _n in refs)
    rows = sorted({ln for _f, ln, _c, _n in refs})
    assert len(refs) == 7, used          # count = 0, count = add(count) x2, {count}, if count, {count + 1}, add(count)
    assert all(ln < at(SRC, "def Other")[0] for ln in rows)                 # not the other page's count
    assert not any('"count"' in lines[ln][c - 1:c + 6] for _f, ln, c, _n in refs)   # strings left alone


def test_component_and_server_function_references():
    line, col = at(SRC, "def Card")
    refs = lsp_refs.references(SRC, None, line, col + 4)
    assert [(ln, SRC.splitlines()[ln][c - 2:c + 4]) for _f, ln, c, _n in refs] == [
        (line, "f Card"), (at(SRC, "<Card")[0], " <Card"), (at(SRC, "</Card")[0], "</Card")]
    line, col = at(SRC, "def add")
    assert len(lsp_refs.references(SRC, None, line, col + 4)) == 3


def test_rename_across_files(tmp_path):
    (tmp_path / "parts.pyweb").write_text('def Badge(text):\n    <span>{text}</span>\n')
    app = 'from parts import Badge\n\n\ndef Home():\n    <Badge text="hi" />\n'
    (tmp_path / "app.pyweb").write_text(app)
    edits = lsp_refs.rename(app, str(tmp_path / "app.pyweb"), 4, 6, "Pill")
    assert sorted((k.rsplit("/", 1)[-1], len(v)) for k, v in edits.items()) == [("app.pyweb", 2), ("parts.pyweb", 1)]
    import pytest
    with pytest.raises(ValueError):
        lsp_refs.rename(app, None, 4, 6, "class")


def test_language_server_requests(tmp_path):
    sent = []
    ls = LanguageServer(sent.append)
    path = tmp_path / "app.pyweb"
    path.write_text(SRC)
    uri = _uri(str(path))
    caps = ls.dispatch("initialize", {})["capabilities"]
    assert caps["referencesProvider"] and caps["renameProvider"] and caps["codeActionProvider"]
    ls.dispatch("textDocument/didOpen", {"textDocument": {"uri": uri, "text": SRC}})
    line, col = at(SRC, "count = 0")
    pos = {"line": line, "character": col}
    refs = ls.dispatch("textDocument/references", {"textDocument": {"uri": uri}, "position": pos})
    assert len(refs) == 7 and refs[0]["uri"] == uri
    prep = ls.dispatch("textDocument/prepareRename", {"textDocument": {"uri": uri}, "position": pos})
    assert prep["placeholder"] == "count"
    edit = ls.dispatch("textDocument/rename", {"textDocument": {"uri": uri}, "position": pos, "newName": "clicks"})
    assert len(edit["changes"][uri]) == 7
    bad = 'from pyweb import App\n\napp = App()\n\n\ndef Card(title):\n    <div>{title}</div>\n\n\n' \
          '@app.page("/")\ndef Home():\n    <Crad title="x" />\n'
    ls.dispatch("textDocument/didChange", {"textDocument": {"uri": uri}, "contentChanges": [{"text": bad}]})
    diags = [m for m in sent if m.get("method") == "textDocument/publishDiagnostics"][-1]["params"]["diagnostics"]
    assert any("did you mean <Card>" in d["message"] for d in diags), diags
    actions = ls.dispatch("textDocument/codeAction", {"textDocument": {"uri": uri}, "context": {"diagnostics": diags}})
    fix = actions[0]
    assert fix["title"] == "Change 'Crad' to 'Card'" and fix["edit"]["changes"][uri][0]["newText"] == "Card"
