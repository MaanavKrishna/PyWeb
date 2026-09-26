"""Server database: parameterized queries, SQLite driver, transactions,
migrations, Postgres guard, model binding, cache backends."""

from __future__ import annotations

import pytest

from pyweb.cache import MemoryCache, RedisCache, get_cache
from pyweb.db import PostgresDB, Query, SQLiteDB, apply, autogen
from pyweb.models import IntegerField, Model, TextField


class Item(Model):
    __table__ = "items"
    id = IntegerField(primary_key=True)
    name = TextField(nullable=False)
    qty = IntegerField(default=0)


@pytest.fixture()
def db():
    d = SQLiteDB(":memory:")
    d.execute(Item.schema_sql())
    Item.bind(d)
    yield d
    Item._db = None
    d.close()


def test_sqlite_crud_roundtrip(db):
    item = Item.create(name="apple", qty=3)
    assert item.id is not None
    got = Item.get(item.id)
    assert got.name == "apple" and got.qty == 3
    got.qty = 9
    got.save()
    assert Item.get(item.id).qty == 9
    assert len(Item.all()) == 1
    assert len(Item.filter(name="apple")) == 1
    assert Item.filter(name="nope") == []
    got.delete()
    assert Item.get(item.id) is None


def test_sql_injection_neutralized(db):
    evil = "x' OR '1'='1"
    Item.create(name=evil, qty=1)
    assert len(Item.all()) == 1
    assert Item.get(Item.all()[0].id).name == evil
    assert Item.filter(name=evil)[0].qty == 1


def test_parameterized_only():
    sql, params = Query("items").where(name="a'b").build_select()
    assert "a'b" not in sql and params == ["a'b"]
    assert "?" in sql
    sql, params = Query("items").build_insert({"name": "x'; DROP--"})
    assert "DROP" not in sql and params == ["x'; DROP--"]
    with pytest.raises(ValueError):
        Query("items; DROP TABLE items--").build_select()
    with pytest.raises(ValueError):
        Query("items").build_update({"name": "x"})
    with pytest.raises(ValueError):
        Query("items").build_delete()


def test_transaction_rollback(db):
    with pytest.raises(RuntimeError):
        with db.transaction():
            Item.create(name="temp")
            raise RuntimeError("boom")
    assert Item.all() == []
    with db.transaction():
        Item.create(name="kept")
    assert len(Item.all()) == 1


def test_migration_up_down(tmp_path):
    outdir = str(tmp_path / "migrations")
    paths = autogen([Item], outdir=outdir)
    import os
    assert os.path.exists(paths["up"]) and os.path.exists(paths["down"])
    d = SQLiteDB(":memory:")
    assert apply(d, outdir, "up") >= 1
    Item.bind(d)
    Item.create(name="mig")
    assert len(Item.all()) == 1
    assert apply(d, outdir, "down") >= 1
    with pytest.raises(Exception):
        Item.all()
    d.close()
    Item._db = None


def test_postgres_guard():
    with pytest.raises(RuntimeError, match="psycopg"):
        PostgresDB("postgres://localhost/x")


def test_model_unbound_errors():
    class Orphan(Model):
        __table__ = "orphan"
        id = IntegerField(primary_key=True)

    Orphan._db = None
    with pytest.raises(RuntimeError, match="not bound"):
        Orphan.all()


def test_cache_memory_ttl_tags():
    c = MemoryCache()
    c.set("a", 1, tags=("t",))
    c.set("b", 2, tags=("t",))
    assert c.get_value("a") == 1
    assert c.invalidate_tag("t") == 2
    assert c.get_value("a") is None and c.get_value("b") is None


def test_cache_memory_ttl_expiry():
    c = MemoryCache()
    c.set("x", "v", ttl=-1)
    assert c.get_value("x") is None
    c.set("y", "v", ttl=60)
    assert c.get_value("y") == "v"
    assert c.delete("y") is True
    assert c.delete("y") is False


def test_cache_redis_guard():
    import sys
    if "redis" in sys.modules:
        pytest.skip("real redis installed; guard path not applicable")
    with pytest.raises(RuntimeError, match="redis"):
        RedisCache.__new__(RedisCache) and get_cache("redis")
    with pytest.raises(ValueError):
        get_cache("nope")


def test_cache_redis_fake_client():
    class Fake:
        def __init__(self):
            self.kv = {}
            self.sets = {}

        def set(self, k, v, ex=None):
            self.kv[k] = v

        def get(self, k):
            return self.kv.get(k)

        def delete(self, *ks):
            n = 0
            for k in ks:
                n += self.kv.pop(k, None) is not None
            return n

        def sadd(self, t, m):
            self.sets.setdefault(t, set()).add(m)

        def smembers(self, t):
            return self.sets.get(t, set())

        def flushdb(self):
            self.kv.clear()

    c = RedisCache(client=Fake())
    c.set("a", {"n": 1}, tags=("t",))
    assert c.get("a") == {"n": 1}
    assert c.invalidate_tag("t") == 1
    assert c.get("a") is None
