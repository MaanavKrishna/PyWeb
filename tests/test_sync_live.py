"""Sync engine + live queries: offline queue, conflicts, minimal fanout."""

from pyweb.livetable import LiveQuery, LiveTable
from pyweb.realtime import Bus
from pyweb.sync import Op, SyncClient, SyncServer


def test_offline_queue_replays_in_order():
    srv = SyncServer()
    cli = SyncClient(srv)
    cli.online = False
    cli.set("a", {"t": "one"}, version=1)
    cli.set("a", {"t": "two"}, version=2)
    assert len(cli.queue) == 2
    assert cli.local["a"] == (2, {"t": "two"})
    out = cli.reconnect()
    assert out == {"replayed": 2, "results": ["applied", "applied"]}
    assert srv.snapshot()["a"]["data"] == {"t": "two"}


def test_offline_delete_then_resurrect():
    srv = SyncServer()
    srv.seed("a", {"t": "x"}, version=1)
    cli = SyncClient(srv)
    cli.online = False
    cli.delete("a", version=1)
    assert "a" not in cli.local
    assert cli.reconnect()["results"] == ["applied"]
    assert "a" not in srv.snapshot()


def test_concurrent_conflict_reports_both():
    srv = SyncServer()
    srv.seed("a", {"t": "server"}, version=1)
    res = srv.apply(Op("set", "a", {"t": "client"}, version=1, actor="client"))
    assert res == "conflict"
    assert len(srv.conflicts) == 1
    c = srv.conflicts[0]
    assert c["server"] == {"t": "server"} and c["client"] == {"t": "client"}
    assert srv.snapshot()["a"]["version"] == 2


def test_stale_write_ignored():
    srv = SyncServer()
    srv.seed("a", {"t": "new"}, version=5)
    assert srv.apply(Op("set", "a", {"t": "old"}, version=3)) == "stale"
    assert srv.snapshot()["a"]["data"] == {"t": "new"}


def test_delete_stale_version_ignored():
    srv = SyncServer()
    srv.seed("a", {"t": "x"}, version=4)
    assert srv.apply(Op("delete", "a", version=2)) == "stale"
    assert "a" in srv.snapshot()


def test_unknown_op_rejected():
    import pytest
    srv = SyncServer()
    with pytest.raises(ValueError):
        srv.apply(Op("merge", "a"))


def test_custom_resolver_client_wins():
    srv = SyncServer(resolver=lambda s, c: ("client", c))
    srv.seed("a", {"t": "s"}, version=1)
    assert srv.apply(Op("set", "a", {"t": "c"}, version=1)) == "conflict"
    assert srv.snapshot()["a"]["data"] == {"t": "c"}


def test_reconnect_pulls_server_state():
    srv = SyncServer()
    srv.seed("srv-only", {"t": 1}, version=1)
    cli = SyncClient(srv)
    cli.online = False
    cli.reconnect()
    assert "srv-only" in cli.local


def test_live_query_only_fires_on_dep_change():
    t = LiveTable("todos", Bus())
    t.set("a", {"title": "a"})
    t.set("b", {"title": "b"})
    q = LiveQuery(t, lambda tr: t.read("a", tr))
    seen = []
    q.subscribe(seen.append)
    assert seen[0] == {"title": "a"}
    fires = q.fires
    t.set("b", {"title": "B2"})
    assert q.fires == fires  # unrelated row: no refire
    t.set("a", {"title": "A2"})
    assert q.fires == fires + 1 and q.value == {"title": "A2"}


def test_live_delete_notifies():
    t = LiveTable("m", Bus())
    t.set("a", {"v": 1})
    q = LiveQuery(t, lambda tr: t.all(tr))
    q.subscribe(lambda v: None)
    fires = q.fires
    t.delete("a")
    assert q.fires == fires + 1 and q.value == {}


def test_live_unsubscribe():
    t = LiveTable("u", Bus())
    t.set("a", {"v": 1})
    q = LiveQuery(t, lambda tr: t.read("a", tr))
    calls = []
    unsub = q.subscribe(calls.append)
    unsub()
    t.set("a", {"v": 2})
    assert len(calls) == 1
