"""ETags, 304, and @app.page(cache=...)."""

import pytest

from pyweb import pagecache
from pyweb.testing import TestClient

APP = '''
from pyweb import App, session, server
from pyweb.forms import Form, Input, Submit

app = App(database="sqlite:///{db}")
RENDERS = []


@server
def add(title: str) -> bool:
    app.db.execute("insert into notes (title) values (?)", (title,))
    return True


@server
def sign_in() -> bool:
    session.login("ada")
    return True


@app.page("/", cache=60)
def Home():
    RENDERS.append(1)
    notes = [r[0] for r in app.db.execute("select title from notes order by id").fetchall()]
    <ul>
        for n in notes:
            <li>{n}</li>
    </ul>


@app.page("/hello", cache=60, vary=["accept-language"])
def Hello():
    RENDERS.append(2)
    <p>hello</p>


@app.page("/plain")
def Plain():
    <p>plain</p>


@app.page("/me", cache=60)
def Me():
    who = session.user()
    <p>{who}</p>
'''


@pytest.fixture
def client(tmp_path, monkeypatch):
    from pyweb import models as M
    monkeypatch.setattr(M._state, "db", None)
    monkeypatch.setattr(pagecache, "_store", None)
    db = tmp_path / "n.db"
    import sqlite3
    sqlite3.connect(db).execute("create table notes (id integer primary key, title text)").connection.commit()
    c = TestClient(source=APP.replace("{db}", str(db)))
    yield c
    M._state.db = None


def test_pages_get_an_etag_and_304(client):
    r = client.get("/plain")
    tag = r.header("ETag")
    assert r.status == 200 and tag.startswith('W/"') and r.header("Cache-Control") == "no-cache"
    again = client.request("GET", "/plain", headers={"If-None-Match": tag})
    assert again.status == 304 and again.body in (b"", "") and again.header("ETag") == tag


def test_cached_pages_render_once_until_their_data_changes(client):
    renders = client.site.app.module.RENDERS
    a = client.get("/")
    b = client.get("/")
    assert a.text == b.text and len(renders) == 1
    assert a.header("Cache-Control") == "public, max-age=60" and b.header("X-PyWeb-Cache") == "hit"
    client.rpc("add", title="milk")                                       # a write to a table it read
    c = client.get("/")
    assert "milk" in c.text and len(renders) == 2


def test_vary_and_personal_pages(client):
    renders = client.site.app.module.RENDERS
    client.request("GET", "/hello", headers={"Accept-Language": "de"})
    client.request("GET", "/hello", headers={"Accept-Language": "de"})
    client.request("GET", "/hello", headers={"Accept-Language": "fr"})
    assert renders.count(2) == 2                                           # one copy per language
    r = client.get("/me")                                                  # read the session: not shared
    assert r.header("Cache-Control") == "no-store" and r.header("X-PyWeb-Cache") is None
    client.rpc("sign_in")
    r = client.get("/")
    assert r.header("X-PyWeb-Cache") is None                               # a session cookie: always fresh
    assert pagecache.invalidate() >= 0


def test_cache_options_are_checked():
    from pyweb.compiler import compile_source
    from pyweb.compiler.errors import CompileError
    bad = 'from pyweb import App\napp = App()\n@app.page("/", cache=-1)\ndef H():\n    <p>x</p>\n'
    with pytest.raises(CompileError, match="cache="):
        compile_source(bad, filename="app.pyweb")
    bad = 'from pyweb import App\napp = App()\n@app.page("/", cache=5, vary=["moon"])\ndef H():\n    <p>x</p>\n'
    with pytest.raises(CompileError, match="vary="):
        compile_source(bad, filename="app.pyweb")
