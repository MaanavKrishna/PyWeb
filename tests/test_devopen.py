"""Click to source: dev builds mark elements with their file and line; other builds don't."""

import os

from pyweb import devopen
from pyweb.compiler import compile_source, pipeline

APP = '''from pyweb import App, component

app = App()


@component
def Box(label):
    <section class="box">{label}</section>


@app.page("/")
def Home():
    <main>
        <h1>Hi</h1>
        <Box label="x" />
    </main>
'''


def _html(dev):
    out = compile_source(APP, filename="app.pyweb", dev=dev)["pages"]["Home"]
    return out["html"] + out["js"]


def test_dev_marks_elements_with_their_line():
    text = _html(True)
    assert 'data-pw-src="app.pyweb:13"' in text and 'data-pw-src="app.pyweb:14"' in text
    assert 'data-pw-src="app.pyweb:8"' in text
    assert 'data-pw-comp="Box"' in text and 'data-pw-comp="Home"' in text


def test_other_builds_have_no_marks():
    for text in (_html(False), _html(None)):
        assert "data-pw-src" not in text and "data-pw-comp" not in text
    assert pipeline._DEV is False                     # a dev compile doesn't leak into the next one


def test_kit_cache_keeps_dev_and_plain_builds_apart():
    src = 'from pyweb import App\nfrom pyweb.ui import Button\napp = App()\n\n@app.page("/")\ndef Home():\n    <Button>Go</Button>\n'
    assert "data-pw-src" in compile_source(src, filename="app.pyweb", dev=True)["pages"]["Home"]["html"]
    assert "data-pw-src" not in compile_source(src, filename="app.pyweb")["pages"]["Home"]["html"]
    assert "data-pw-src" in compile_source(src, filename="app.pyweb", dev=True)["pages"]["Home"]["html"]
    assert pipeline._KIT_LIBS


def test_test_client_builds_have_no_marks(tmp_path):
    from pyweb.testing import TestClient
    (tmp_path / "app.pyweb").write_text(APP)
    assert "data-pw-src" not in TestClient(str(tmp_path / "app.pyweb")).get("/").text


def test_only_app_and_kit_files_resolve(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.pyweb").write_text("x\n")
    (tmp_path / "other.pyweb").write_text("x\n")
    (app / "notes.txt").write_text("x\n")
    assert devopen.resolve("app.pyweb:3", str(app)) == (os.path.realpath(app / "app.pyweb"), 3, True)
    assert devopen.resolve("../other.pyweb:1", str(app)) is None
    assert devopen.resolve(str(tmp_path / "other.pyweb") + ":1", str(app)) is None
    assert devopen.resolve("notes.txt:1", str(app)) is None
    assert devopen.resolve("app.pyweb:x", str(app)) is None
    kit = os.path.join(pipeline.KIT_DIR, "button.pyweb")
    assert devopen.resolve(kit + ":2", str(app))[2] is False
    # the innermost app line wins over kit lines inside it
    assert devopen.pick([kit + ":2", "app.pyweb:3"], str(app))[1] == 3


def test_editor_command(monkeypatch):
    monkeypatch.setenv("PYWEB_EDITOR", "myedit --line {line} {file}")
    assert devopen.command("/a b.pyweb", 4) == ["myedit", "--line", "4", "/a b.pyweb"]
    monkeypatch.setenv("PYWEB_EDITOR", "myedit")
    assert devopen.command("/a.pyweb", 4) == ["myedit", "/a.pyweb"]
    monkeypatch.delenv("PYWEB_EDITOR")
    monkeypatch.setattr(devopen.shutil, "which", lambda exe: exe == "cursor")
    assert devopen.command("/a.pyweb", 4) == ["cursor", "-g", "/a.pyweb:4"]
    monkeypatch.setattr(devopen.shutil, "which", lambda exe: None)
    assert devopen.command("/a.pyweb", 4) is None
    out = devopen.open_source(["app.pyweb:1"], os.path.dirname(os.path.abspath(__file__)))
    assert out == {"error": "that element has no source in this app"}
