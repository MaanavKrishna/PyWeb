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


PATCH_APP = '''from pyweb import App, live, server
from pyweb.db import connect

app = App()
db = connect("sqlite:///patch.db")
db.execute("create table if not exists items (id integer primary key, title text, done integer default 0)")
if not db.execute("select count(*) as n from items").dicts()[0]["n"]:
    for i in range(30):
        db.execute("insert into items (title) values (?)", (f"item {i}",))


@server
def toggle(item_id: int) -> None:
    db.execute("update items set done = 1 - done where id = ?", (item_id,))


@server
def add(title: str) -> None:
    db.execute("insert into items (title) values (?)", (title,))


@app.page("/")
def Items():
    items = live(db, "select id, title, done from items order by id")
    <ul id="list">
        for t in items:
            <li id={"i" + str(t["id"])} class={"done" if t["done"] else ""}>{t["title"]}</li>
    </ul>
    <button id="t5" onclick={lambda: toggle(5)}>toggle 5</button>
    <button id="add" onclick={lambda: add("new one")}>add</button>
'''

TRACK = """
window.__frames = [];
window.__sockets = [];
const Real = window.WebSocket;
window.WebSocket = class extends Real {
  constructor(...a) {
    super(...a);
    window.__sockets.push(this);
    this.addEventListener("message", (e) => window.__frames.push(e.data));
  }
};
"""


def test_a_change_patches_one_row_and_keeps_the_rest(browser, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.pyweb").write_text(PATCH_APP)
    with serve(str(tmp_path / "app.pyweb")) as url:
        a, b = browser.new_page(), browser.new_page()
        b.add_init_script(TRACK)
        for page in (a, b):
            page.goto(url)
            ready(page)
        b.evaluate("document.getElementById('i1').__mark = 'kept'; document.getElementById('i5').__mark = 'kept'")
        a.click("#t5")
        expect(b.locator("#i5")).to_have_class("done")
        frames = [f for f in b.evaluate("window.__frames") if '"ops"' in f]
        assert len(frames) == 1 and len(frames[0]) < 300 and "item 6" not in frames[0]   # one row on the wire
        assert b.evaluate("document.getElementById('i1').__mark") == "kept"                # other rows' DOM kept
        assert b.evaluate("document.getElementById('i5').__mark") is None                   # the changed row redrawn
        a.click("#add")
        expect(b.locator("#list li")).to_have_count(31)
        expect(b.locator("#list li").last).to_have_text("new one")


def test_a_dropped_socket_reconnects_and_catches_up(browser, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.pyweb").write_text(PATCH_APP)
    with serve(str(tmp_path / "app.pyweb")) as url:
        a, b = browser.new_page(), browser.new_page()
        b.add_init_script(TRACK)
        for page in (a, b):
            page.goto(url)
            ready(page)
        b.evaluate("window.__sockets[0].close()")
        a.click("#t5")                                   # happens while b is disconnected
        a.click("#add")
        expect(b.locator("#i5")).to_have_class("done", timeout=8000)
        expect(b.locator("#list li")).to_have_count(31)
        assert b.evaluate("window.__sockets.length") == 2


MODEL_APP = '''from pyweb import App, Field, Model, server

app = App(database="sqlite:///orders.db")


class Order(Model):
    item: str = Field(min=1, max=80)
    status: str = "new"


@server
def place(item: str) -> None:
    Order.create(item=item)


@server
def ship(order_id: int) -> None:
    Order.where(id=order_id).update(status="shipped")


@app.page("/")
def Orders():
    orders = Order.query().order("-id").limit(50).live()
    waiting = len([o for o in orders if o["status"] == "new"])     # derived: follows orders in the browser
    item = ""

    def order():
        place(item)
        item = ""

    <h1 id="title">Orders ({waiting} waiting)</h1>
    <form onsubmit={order}><input id="item" bind={item} /><button>Order</button></form>
    <ul id="orders">
        for o in orders:
            <li>{o["item"]}: {o["status"]}
                if o["status"] == "new":
                    <button class="ship" onclick={lambda: ship(o["id"])}>Ship</button>
            </li>
    </ul>
'''


def test_values_derived_from_a_live_model_query_follow_it(browser, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYWEB_WORKER", "0")
    (tmp_path / "app.pyweb").write_text(MODEL_APP)
    errors = []
    with serve(str(tmp_path / "app.pyweb")) as url:
        a, b = browser.new_page(), browser.new_page()
        for page in (a, b):
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url)
            ready(page)
        expect(b.locator("#title")).to_have_text("Orders (0 waiting)")
        for item in ("Book", "Pen"):
            a.fill("#item", item)
            a.press("#item", "Enter")
        expect(b.locator("#orders li")).to_have_count(2)
        expect(b.locator("#title")).to_have_text("Orders (2 waiting)")
        b.locator(".ship").first.click()                  # ships Pen (newest first)
        expect(a.locator("#orders li").first).to_have_text("Pen: shipped")
        expect(a.locator("#title")).to_have_text("Orders (1 waiting)")
    assert errors == []
