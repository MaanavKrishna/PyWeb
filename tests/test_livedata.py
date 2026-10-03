"""Live data: write tracking in pyweb.db, shared live queries, and the page integration."""

import json
import re
import textwrap

import pytest

from pyweb import livedata
from pyweb.compiler import compile_source
from pyweb.compiler.errors import CompileError
from pyweb.db import connect
from pyweb.realtime import Bus, use_bus
from pyweb.testing import TestClient


@pytest.fixture
def bus(monkeypatch):
    """A fresh bus and registry; the registry is driven by hand (no watcher thread)."""
    from pyweb import realtime
    old = realtime.current_bus()
    fresh = use_bus(Bus())
    registry = livedata._Registry()
    monkeypatch.setattr(livedata, "REGISTRY", registry)
    monkeypatch.setattr(livedata._Registry, "_start", lambda self: None)
    monkeypatch.setattr(livedata, "_enabled", True)
    yield fresh
    use_bus(old)


@pytest.fixture
def db(tmp_path):
    d = connect(f"sqlite:///{tmp_path / 'app.db'}")
    d.execute("create table todos (id integer primary key, title text, done integer default 0)")
    return d


def table_messages(bus, db, table):
    return [m for _seq, m in bus.since(f"{livedata.TABLE_CHANNEL}{livedata.database_id(db)}:{table}", 0)]


@pytest.mark.parametrize("sql, table", [
    ("INSERT INTO todos (title) VALUES (?)", "todos"),
    ("insert or replace into Todos values (1)", "todos"),
    ('update "todos" set done = 1', "todos"),
    ("UPDATE OR IGNORE main.todos SET x = 1", "todos"),
    ("delete from `todos` where id = ?", "todos"),
    ("REPLACE INTO todos VALUES (1)", "todos"),
    ("with old as (select id from todos) delete from todos where id in (select id from old)", "todos"),
    ("TRUNCATE TABLE logs", "logs"),
    ("drop table if exists temp_x", "temp_x"),
    ("select * from todos", None),
    ("select 'insert into fake'", None),
])
def test_written_table(sql, table):
    assert livedata.written_table(sql) == table


def test_read_tables():
    assert livedata.read_tables("select t.*, u.name from todos t join users u on u.id = t.owner "
                                "where t.title != 'from nowhere'") == ["todos", "users"]
    assert livedata.read_tables("select count(*) from (select 1) as x") == []


def test_writes_announce_their_table_after_commit(bus, db):
    db.execute("insert into todos (title) values (?)", ("a",))
    assert table_messages(bus, db, "todos") == [{"table": "todos"}]
    with db.transaction():
        db.execute("update todos set done = 1")
        db.execute("delete from todos where title = ?", ("zzz",))
        assert len(table_messages(bus, db, "todos")) == 1            # nothing until COMMIT
    assert len(table_messages(bus, db, "todos")) == 2                # one announcement per table per commit
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.execute("insert into todos (title) values (?)", ("b",))
            raise RuntimeError("rolled back")
    assert len(table_messages(bus, db, "todos")) == 2                # rolled back: no announcement
    db.notify("todos", "Other")                                       # writes made elsewhere
    assert len(table_messages(bus, db, "todos")) == 3 and table_messages(bus, db, "other") == [{"table": "other"}]


def test_nothing_is_announced_until_live_data_is_used(bus, db, monkeypatch):
    monkeypatch.setattr(livedata, "_enabled", False)
    db.execute("insert into todos (title) values (?)", ("a",))
    assert table_messages(bus, db, "todos") == []


def test_one_rerun_per_change_shared_by_every_viewer(bus, db, monkeypatch):
    runs = []
    original = livedata._Entry.run
    monkeypatch.setattr(livedata._Entry, "run", lambda self: runs.append(self.key) or original(self))
    sql = "select title from todos where done = ? order by id"
    first = livedata.REGISTRY.add(db, sql, (0,), ["todos"])
    first.run()
    assert livedata.REGISTRY.add(db, sql, (0,), ["todos"]) is first   # same query: one entry
    other = livedata.REGISTRY.add(db, sql, (1,), ["todos"])
    other.run()
    runs.clear()
    for title in ("a", "b", "c"):                                      # a burst of writes...
        db.execute("insert into todos (title) values (?)", (title,))
    livedata.REGISTRY.check()
    assert sorted(runs) == sorted([first.key, other.key])             # ...one re-run per query
    chan = livedata.LIVE_CHANNEL + first.key
    assert [m["rows"] for _s, m in bus.since(chan, 0)] == [[{"title": "a"}, {"title": "b"}, {"title": "c"}]]
    assert bus.since(livedata.LIVE_CHANNEL + other.key, 0) == []     # its rows didn't change: nothing sent
    db.execute("update todos set title = title")                      # a write that changes nothing visible
    livedata.REGISTRY.check()
    assert len(bus.since(chan, 0)) == 1


def test_forgotten_after_idle_unless_watched(bus, db, monkeypatch):
    entry = livedata.REGISTRY.add(db, "select * from todos", (), ["todos"])
    livedata.REGISTRY.watching(entry.key, 1)
    monkeypatch.setattr(livedata, "IDLE_SECONDS", -1)
    livedata.REGISTRY.check()
    assert entry.key in livedata.REGISTRY.entries
    livedata.REGISTRY.watching(entry.key, -1)
    livedata.REGISTRY.check()
    assert entry.key not in livedata.REGISTRY.entries


def test_specs_are_signed(bus, db):
    token = livedata.make_spec("secret", livedata.database_id(db), "select * from todos", (), ["todos"])
    key = livedata.adopt_spec(token, "secret")
    assert key in livedata.REGISTRY.entries
    assert livedata.adopt_spec(token, "other secret") is None
    assert livedata.adopt_spec(token[:-1] + "0", "secret") is None
    forged = livedata.make_spec("secret", "nope", "select * from todos", (), ["todos"])
    assert livedata.adopt_spec(forged, "secret") is None               # an unknown database


APP = textwrap.dedent('''
    from pyweb import App, live, server
    from pyweb.db import connect

    app = App()
    db = connect("sqlite:///live.db")
    db.execute("create table if not exists todos (id integer primary key, title text)")


    @server
    def add(title: str) -> None:
        db.execute("insert into todos (title) values (?)", (title,))


    @app.page("/")
    def Todos():
        todos = live(db, "select title from todos order by id")
        count = live(db, "select count(*) as n from todos", tables=["todos"])

        <ul>
            for t in todos:
                <li>{t["title"]}</li>
        </ul>
        <p>{count[0]["n"]}</p>
''').lstrip()


def test_live_page_variables(bus, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = TestClient(source=APP)
    client.rpc("add", title="milk")
    html = client.get("/").text
    assert "<li>milk</li>" in html and "<p>1</p>" in html
    state = json.loads(re.search(r'id="pw-state" type="application/json">(.*?)</script>', html).group(1))
    assert state["todos"] == [{"title": "milk"}]
    meta = state["$live:todos"]
    assert set(meta) == {"feed", "spec", "version"}
    js = compile_source(APP)["pages"]["Todos"]["js"]
    assert '$live(todos, $s["$live:todos"]);' in js and '$live(count, $s["$live:count"]);' in js
    # The browser follows the feed: new rows arrive after a write.
    client.rpc("add", title="eggs")
    livedata.REGISTRY.check()
    events = client.get(f"/__pyweb/events?feed={meta['feed']}&live={meta['spec']}").text
    data = [json.loads(line[5:]) for line in events.splitlines() if line.startswith("data:")]
    assert data[-1]["rows"] == [{"title": "milk"}, {"title": "eggs"}]


def test_live_is_for_pages_and_layouts():
    src = APP.replace("        <p>{count[0][\"n\"]}</p>\n", "") + textwrap.dedent('''

        def Counter():
            rows = live(db, "select * from todos")
            <p>{len(rows)}</p>
    ''')
    with pytest.raises(CompileError, match="call it in a page or layout"):
        compile_source(src)


def test_huge_live_queries_are_refused(bus, db, monkeypatch):
    monkeypatch.setattr(livedata, "MAX_ROWS", 2)
    for t in "abc":
        db.execute("insert into todos (title) values (?)", (t,))
    entry = livedata.REGISTRY.add(db, "select * from todos", (), ["todos"])
    with pytest.raises(ValueError, match="add a LIMIT"):
        entry.run()


def test_live_needs_a_table(bus, db, tmp_path, monkeypatch):
    from pyweb import context
    monkeypatch.setattr(context, "current", lambda: None)
    with pytest.raises(ValueError, match="pass tables="):
        livedata.live(db, "select 1")
