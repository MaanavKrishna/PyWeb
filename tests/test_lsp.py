"""The language server: analysis functions and the stdio protocol."""

import json
import subprocess
import sys
import textwrap

from pyweb import lsp

APP = textwrap.dedent('''
    from pyweb import App, server
    from widgets import Card

    app = App()


    @server
    def latest(limit: int = 5) -> list:
        return []


    @app.page("/")
    def Home():
        count = 0
        posts = latest()

        def inc():
            count += 1

        <main>
            <button onclick={inc}>{count}</button>
            <Card title="Hi" />
        </main>
''').lstrip()

WIDGETS = textwrap.dedent('''
    def Card(title, subtitle="", children=None):
        <section><h2>{title}</h2>{children}</section>
''').lstrip()


def project(tmp_path):
    (tmp_path / "widgets.pyweb").write_text(WIDGETS)
    path = tmp_path / "app.pyweb"
    path.write_text(APP)
    return str(path)


def pos(text, needle, line_hint=None):
    """0-based (line, col) of ``needle``'s first character."""
    for i, line in enumerate(text.splitlines()):
        if needle in line and (line_hint is None or line_hint in line):
            return i, line.index(needle)
    raise AssertionError(needle)


def test_clean_file_has_no_diagnostics(tmp_path):
    path = project(tmp_path)
    assert lsp.diagnostics(APP, path) == []


def test_compile_errors_become_diagnostics_on_the_right_line(tmp_path):
    path = project(tmp_path)
    broken = APP.replace("count += 1", "count += latest")
    d = lsp.diagnostics(broken, path)[0]
    assert d["severity"] == 1 and "latest" in d["message"]
    assert d["range"]["start"]["line"] == pos(broken, "count += latest")[0]
    markup = APP.replace("<button onclick={inc}>{count}</button>", "<button onclick={inc}>{count}</div>")
    d = lsp.diagnostics(markup, path)[0]
    assert d["range"]["start"]["line"] == pos(markup, "</div>")[0] and "mismatched" in d["message"]


def test_errors_in_imported_files_point_at_the_import(tmp_path):
    path = project(tmp_path)
    (tmp_path / "widgets.pyweb").write_text("def Card(title):\n    <p>{title}</div>\n")
    d = lsp.diagnostics(APP, path)[0]
    assert d["message"].startswith("widgets.pyweb:2:")
    assert d["range"]["start"]["line"] == pos(APP, "from widgets")[0]


def test_security_findings_are_warnings():
    src = ('from pyweb import server\n\n@server\ndef find(q: str) -> list:\n'
           '    return db.execute(f"SELECT * FROM t WHERE name = {q}")\n')
    d = lsp.diagnostics(src, None)[0]
    assert d["severity"] == 2 and d["code"] == "sql-injection" and d["range"]["start"]["line"] == 4


def test_hover_explains_placement_rpc_and_components(tmp_path):
    path = project(tmp_path)
    a = lsp.Analysis(APP, path)
    line, col = pos(APP, "count", "count = 0")
    assert lsp.hover(APP, path, line, col, a).startswith("**count** runs in the browser: reactive state")
    line, col = pos(APP, "posts")
    assert "runs on the server" in lsp.hover(APP, path, line, col, a)
    line, col = pos(APP, "latest", "def latest")
    h = lsp.hover(APP, path, line, col, a)
    assert "def latest(limit: int) -> list" in h and "POST /__pyweb/rpc/latest" in h
    line, col = pos(APP, "Card", "<Card")
    h = lsp.hover(APP, path, line, col + 1, a)
    assert "Card(title, subtitle, children)" in h and "widgets.pyweb:1" in h


def test_completion_offers_components_tags_props_and_exports(tmp_path):
    path = project(tmp_path)
    a = lsp.Analysis(APP, path)
    labels = [i["label"] for i in lsp.completions("<", path, 0, 1, a)]
    assert labels[0] == "Card" and "button" in labels and "section" in labels
    text = '        <Card title="x" '
    props = [i["label"] for i in lsp.completions(text, path, 0, len(text), a)]
    assert props == ["subtitle"]  # title already used; children come from nesting
    text = "        <input "
    assert "bind" in [i["label"] for i in lsp.completions(text, path, 0, len(text), a)]
    text = "from pyweb import "
    assert "subscribe" in [i["label"] for i in lsp.completions(text, path, 0, len(text), a)]
    assert lsp.completions("x = 1", path, 0, 5, a) == []


def test_definition_across_files(tmp_path):
    path = project(tmp_path)
    line, col = pos(APP, "Card", "<Card")
    file, ln = lsp.definition(APP, path, line, col + 1)
    assert file.endswith("widgets.pyweb") and ln == 0
    line, col = pos(APP, "latest", "posts = latest")
    assert lsp.definition(APP, path, line, col + 1) == (path, pos(APP, "def latest")[0])


def test_symbols_outline():
    kinds = {s["name"]: s["kind"] for s in lsp.symbols(APP + "\nclass Post:\n    pass\n")}
    assert kinds == {"latest": "server function", "Home": "page", "Post": "class"}


def _frame(msg):
    body = json.dumps(msg).encode()
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body


def _read_all(raw):
    out = []
    while raw:
        head, _, rest = raw.partition(b"\r\n\r\n")
        n = int(head.split(b":")[1])
        out.append(json.loads(rest[:n]))
        raw = rest[n:]
    return out


def test_stdio_session(tmp_path):
    path = project(tmp_path)
    uri = "file://" + path
    broken = APP.replace("{count}</button>", "{count}</div>")
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"capabilities": {}}},
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        {"jsonrpc": "2.0", "method": "textDocument/didOpen",
         "params": {"textDocument": {"uri": uri, "languageId": "pyweb", "version": 1, "text": APP}}},
        {"jsonrpc": "2.0", "id": 2, "method": "textDocument/hover",
         "params": {"textDocument": {"uri": uri}, "position": dict(zip(("line", "character"), pos(APP, "count", "count = 0")))}},
        {"jsonrpc": "2.0", "method": "textDocument/didChange",
         "params": {"textDocument": {"uri": uri, "version": 2}, "contentChanges": [{"text": broken}]}},
        {"jsonrpc": "2.0", "id": 3, "method": "textDocument/completion",
         "params": {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 0}}},
        {"jsonrpc": "2.0", "id": 4, "method": "textDocument/definition",
         "params": {"textDocument": {"uri": uri}, "position": dict(zip(("line", "character"), pos(APP, "Card", "<Card")))}},
        {"jsonrpc": "2.0", "id": 5, "method": "textDocument/documentSymbol", "params": {"textDocument": {"uri": uri}}},
        {"jsonrpc": "2.0", "id": 6, "method": "shutdown"},
        {"jsonrpc": "2.0", "method": "exit"},
    ]
    proc = subprocess.run([sys.executable, "-m", "pyweb.cli", "lsp"], input=b"".join(map(_frame, msgs)),
                          capture_output=True, timeout=60, cwd=tmp_path)
    out = _read_all(proc.stdout)
    by_id = {m["id"]: m for m in out if "id" in m}
    assert by_id[1]["result"]["capabilities"]["hoverProvider"] is True
    assert "runs in the browser" in by_id[2]["result"]["contents"]["value"]
    diags = [m["params"]["diagnostics"] for m in out if m.get("method") == "textDocument/publishDiagnostics"]
    assert diags[0] == [] and "mismatched" in diags[1][0]["message"]
    assert by_id[4]["result"]["uri"].endswith("/widgets.pyweb")
    assert [s["name"] for s in by_id[5]["result"]] == ["latest", "Home"]
    assert by_id[6]["result"] is None and proc.returncode == 0
