"""TestClient.render / find / a11y and assert_snapshot."""

import pytest

from pyweb.testing import TestClient, assert_snapshot

APP = '''
from pyweb import App

app = App(title="Helpers")


def Badge(label: str, tone: str = "info"):
    <span class={["badge", tone]}>{label}</span>


@app.page("/")
def Home():
    todos = [{"title": "milk", "done": True}, {"title": "bread", "done": False}]
    <h1>Todos</h1>
    <ul class="todos">
        for t in todos:
            <li class={"done" if t["done"] else "open"}>{t["title"]}</li>
    </ul>
    <Badge label="2 left" />


@app.page("/bad")
def Bad():
    <img src="/x.png" />
    <input name="q" />
    <button></button>
'''


@pytest.fixture
def client():
    return TestClient(source=APP)


def test_find_and_render(client):
    r = client.get("/")
    assert [li.text for li in r.find_all("ul.todos li")] == ["milk", "bread"]
    assert r.find("li.done").text == "milk" and r.find("h1").text == "Todos"
    badge = client.render("Badge", label="new", tone="warn").find("span")
    assert badge.text == "new" and badge.classes == ["badge", "warn"]
    with pytest.raises(KeyError):
        client.render("Nope")


def test_a11y_on_the_rendered_page(client):
    assert client.a11y("/") == []
    rules = sorted(p["rule"] for p in client.a11y("/bad"))
    assert rules == ["button-name", "img-alt", "label"]


def test_snapshots(tmp_path, monkeypatch):
    folder = str(tmp_path / "snaps")
    assert_snapshot("<p>hi</p>", "greeting", folder=folder)               # first run writes it
    assert_snapshot("<p>hi</p>", "greeting", folder=folder)
    with pytest.raises(AssertionError, match="-<p>hi</p>"):
        assert_snapshot("<p>bye</p>", "greeting", folder=folder)
    monkeypatch.setenv("PYWEB_UPDATE_SNAPSHOTS", "1")
    assert_snapshot("<p>bye</p>", "greeting", folder=folder)
    monkeypatch.delenv("PYWEB_UPDATE_SNAPSHOTS")
    assert_snapshot("<p>bye</p>", "greeting", folder=folder)


def test_dev_debug_starts_debugpy(monkeypatch, capsys):
    import sys
    import types

    from pyweb import cli
    calls = []
    fake = types.SimpleNamespace(listen=lambda addr: calls.append(("listen", addr)),
                                 wait_for_client=lambda: calls.append(("wait",)))
    monkeypatch.setitem(sys.modules, "debugpy", fake)
    cli.start_debugger(5679, wait=True)
    assert calls == [("listen", ("127.0.0.1", 5679)), ("wait",)]
    assert "attach to 127.0.0.1:5679" in capsys.readouterr().out
    monkeypatch.setitem(sys.modules, "debugpy", None)
    with pytest.raises(SystemExit, match="pip install debugpy"):
        cli.start_debugger()


SM_APP = '''
from pyweb import App

app = App()


@app.page("/")
def Home():
    count = 0

    def add():
        count += 1

    <button onclick={add}>Clicked {count}</button>
'''


def test_source_maps_point_into_the_pyweb_file(tmp_path, monkeypatch):
    import json

    from pyweb.hosting import Site
    from pyweb.sourcemap import _decode
    (tmp_path / "app.pyweb").write_text(SM_APP)
    site = Site(str(tmp_path / "app.pyweb"), debug=True, dev=True)
    status, _h, js = site.respond("GET", "/static/Home.js", {})
    assert js.decode().rstrip().endswith("//# sourceMappingURL=Home.js.map")
    status, _h, raw = site.respond("GET", "/static/Home.js.map", {})
    smap = json.loads(raw)
    assert status == 200 and smap["version"] == 3 and smap["sources"] == ["/app.pyweb"]
    assert "def add():" in smap["sourcesContent"][0]
    lines, cur = [], 0
    for seg in smap["mappings"].split(";"):
        cur += _decode(seg)[2]
        lines.append(cur + 1)
    body = js.decode().split("\n")
    at = next(i for i, text in enumerate(body) if "function add(" in text)
    assert lines[at] == 11                                  # `def add():` in the .pyweb file
    # Not in production serving: a plain Site has no map, and builds link it only when asked.
    plain = Site(str(tmp_path / "app.pyweb"), debug=True)
    assert plain.respond("GET", "/static/Home.js.map", {})[0] == 404
    from pyweb.build import build
    from pyweb.compiler import compile_source
    out = tmp_path / "dist"
    build(compile_source(SM_APP, filename="app.pyweb"), str(out), production=False, sourcemaps=True)
    built = (out / "static" / "Home.js").read_text()
    assert "sourceMappingURL=Home.js.map" in built
    assert "sourcesContent" not in json.loads((out / "static" / "Home.js.map").read_text())
