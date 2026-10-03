"""Live queries in real browsers: a write on one page shows up on another."""

from playwright.sync_api import expect

from pyweb.testing import serve
from tests.e2e.conftest import ready

APP = '''from pyweb import App, live, server
from pyweb.db import connect

app = App()
db = connect("sqlite:///live.db")
db.execute("create table if not exists todos (id integer primary key, title text, done integer default 0)")


@server
def add(title: str) -> None:
    db.execute("insert into todos (title) values (?)", (title,))


@server
def finish_all() -> None:
    with db.transaction():
        db.execute("update todos set done = 1")
        db.execute("delete from todos where title = ?", ("tmp",))


@app.page("/")
def Todos():
    todos = live(db, "select id, title, done from todos order by id")
    remaining = live(db, "select count(*) as n from todos where done = 0")
    draft = ""

    def submit():
        add(draft)
        draft = ""

    <ul id="list">
        for t in todos:
            <li class={"done" if t["done"] else ""}>{t["title"]}</li>
    </ul>
    <p id="open">{remaining[0]["n"]} open</p>
    <form onsubmit={submit}><input id="new" bind={draft} /><button>Add</button></form>
    <button id="all" onclick={lambda: finish_all()}>finish all</button>
'''


def test_writes_reach_every_open_page(browser, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.pyweb").write_text(APP)
    errors = []
    with serve(str(tmp_path / "app.pyweb")) as url:
        a, b = browser.new_page(), browser.new_page()
        for page in (a, b):
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url)
            ready(page)
        a.fill("#new", "milk")
        a.press("#new", "Enter")
        expect(b.locator("#list li")).to_have_text(["milk"])
        expect(b.locator("#open")).to_have_text("1 open")
        a.fill("#new", "tmp")
        a.press("#new", "Enter")
        expect(b.locator("#list li")).to_have_count(2)
        b.click("#all")                                    # a transaction: both changes arrive together
        expect(a.locator("#list li")).to_have_text(["milk"])
        expect(a.locator("#list li.done")).to_have_count(1)
        expect(a.locator("#open")).to_have_text("0 open")
        b.reload()
        ready(b)
        expect(b.locator("#list li.done")).to_have_text(["milk"])
    assert errors == []
