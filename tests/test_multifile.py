"""Apps split across several .pyweb files."""

import os
import textwrap

import pytest

from pyweb.compiler import compile_source
from pyweb.compiler.errors import CompileError
from pyweb.testing import TestClient

APP = '''
from pyweb import App
from widgets import Card, Badge as Tag, save, SHOP

app = App(title="Shop")
COLOR = "app-blue"


@app.page("/")
def Home():
    saved = ""

    async def keep():
        saved = await save("lamp")

    <main class={COLOR}>
        <h1>{SHOP}</h1>
        <Card title="Lamp">
            <p id="body">Bright.</p>
        </Card>
        <Tag label="sale" />
        <button id="keep" onclick={keep}>Keep</button>
        <p id="saved">{saved}</p>
    </main>
'''

WIDGETS = '''
from pyweb import component, server
from ui.icons import Icon

SHOP = "Lamp shop"
COLOR = "widget-red"
_store = []


@server
def save(item: str) -> str:
    _store.append(item)
    return f"saved {item} ({len(_store)})"


def Badge(label):
    clicks = 0

    def bump():
        clicks += 1

    <button class={"badge " + COLOR} onclick={bump}>{label} {clicks}</button>


def Card(title, children):
    <section class={COLOR}>
        <h2><Icon name="star" /> {title}</h2>
        {children}
        <Badge label="new" />
    </section>
'''

ICONS = '''
SYMBOLS = {"star": "*", "heart": "<3"}


def Icon(name):
    <i class="icon">{SYMBOLS[name]}</i>
'''


def write(tmp_path, files):
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text).lstrip())
    return str(tmp_path / "app.pyweb")


@pytest.fixture
def shop(tmp_path):
    return write(tmp_path, {"app.pyweb": APP, "widgets.pyweb": WIDGETS, "ui/icons.pyweb": ICONS})


def compile_file(path):
    with open(path) as fh:
        return compile_source(fh.read(), filename=path)


def test_imported_components_compile_into_scoped_blocks(shop):
    out = compile_file(shop)
    js = out["pages"]["Home"]["js"]
    # Each file's names live in their own scope: both COLOR constants survive.
    assert 'const { Card, Badge: Tag } = (() => {' in js
    block = js.split("// widgets.pyweb", 1)[1].split("})();\nconst COLOR", 1)[0]
    assert '"widget-red"' in block and "app-blue" not in block
    assert 'const COLOR = "app-blue";' in js  # the app's own constant, outside the block
    assert "const { Icon } = (() => {" in js
    assert '$rpc("save"' in js
    assert [lib.name for lib in out["libraries"]] == ["ui.icons", "widgets"]
    html = out["pages"]["Home"]["html"]
    assert '<section class="widget-red"><h2><i class="icon">*</i> Lamp</h2><p id="body">Bright.</p>' in html
    assert "<h1>Lamp shop</h1>" in html


def test_server_renders_and_calls_functions_from_other_files(shop, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = TestClient(shop)
    page = client.get("/")
    assert page.status == 200
    assert '<main class="app-blue">' in page.text and '<i class="icon">*</i>' in page.text
    assert '<button class="badge widget-red">sale 0</button>' in page.text
    assert client.rpc("save", item="lamp") == "saved lamp (1)"


def test_dist_build_includes_imported_files(shop, tmp_path):
    from pyweb.build import build
    out = tmp_path / "dist"
    with open(shop) as fh:
        source = fh.read()
    build(compile_source(source, filename=shop), str(out), source=source, app_dir=str(tmp_path))
    assert (out / "widgets.pyweb").exists() and (out / "ui" / "icons.pyweb").exists()
    client = TestClient(str(out))
    assert "Lamp shop" in client.get("/").text


@pytest.mark.parametrize("files, message", [
    ({"app.pyweb": "from a import X\n", "a.pyweb": "from b import Y\n", "b.pyweb": "from a import X\n"},
     "circular import between .pyweb files: app.pyweb -> a.pyweb -> b.pyweb -> a.pyweb"),
    ({"app.pyweb": "from w import Nope\n", "w.pyweb": "X = 1\n"}, "w.pyweb has no 'Nope'"),
    ({"app.pyweb": "from w import go as run\n", "w.pyweb": "from pyweb import server\n@server\ndef go() -> int:\n    return 1\n"},
     "without `as`"),
    ({"app.pyweb": "from w import X\n",
      "w.pyweb": "from pyweb import App\napp = App()\nX = 1\n@app.page('/')\ndef P():\n    <p>x</p>\n"},
     "pages belong in the app file"),
    ({"app.pyweb": "from pyweb import server\nfrom w import go\n@server\ndef go() -> int:\n    return 2\n",
      "w.pyweb": "from pyweb import server\n@server\ndef go() -> int:\n    return 1\n"},
     "two @server functions are named 'go'"),
])
def test_import_errors(tmp_path, files, message):
    path = write(tmp_path, files)
    with pytest.raises(CompileError, match=message.replace("(", r"\(").replace(")", r"\)")):
        compile_file(path)


def test_duplicate_rpc_names_across_files(tmp_path):
    path = write(tmp_path, {
        "app.pyweb": "from a import A\nfrom b import B\n",
        "a.pyweb": "from pyweb import server\n@server\ndef go() -> int:\n    return 1\ndef A():\n    <p>a</p>\n",
        "b.pyweb": "from pyweb import server\n@server\ndef go() -> int:\n    return 2\ndef B():\n    <p>b</p>\n",
    })
    with pytest.raises(CompileError, match="two @server functions are named 'go'"):
        compile_file(path)


def test_errors_in_an_imported_file_name_that_file(tmp_path):
    path = write(tmp_path, {"app.pyweb": "from w import Card\n", "w.pyweb": "def Card():\n    <p>x</div>\n"})
    with pytest.raises(SyntaxError) as exc:  # markup errors are SyntaxErrors that name the file
        compile_file(path)
    assert os.path.basename(exc.value.filename) == "w.pyweb" and exc.value.lineno == 2
    path = write(tmp_path, {"app.pyweb": "from pyweb import App\nfrom w import Card\napp = App()\n"
                                         "@app.page('/')\ndef H():\n    <Card />\n",
                            "w.pyweb": "import sqlite3\n\ndef Card():\n    n = 0\n    def go():\n"
                                "        sqlite3.connect('x')\n    <button onclick={go}>{n}</button>\n"})
    from pyweb import mcp
    rec = mcp.tool_check({"path": path})
    assert rec["ok"] is False
    assert rec["errors"][0]["file"].endswith("w.pyweb") and rec["errors"][0]["line"] == 6


def test_plain_python_modules_still_import_normally(tmp_path):
    path = write(tmp_path, {"app.pyweb": "from helpers import tax\nfrom pyweb import App\napp = App()\n"
                                         "@app.page('/')\ndef H():\n    t = tax(10)\n    <p>{t}</p>\n",
                            "helpers.py": "def tax(x):\n    return x * 2\n"})
    assert "<p>20</p>" in TestClient(path).get("/").text
