"""Models + DB toolkit: CRUD, query builder, transactions, migrations."""

import sqlite3

import pytest

from pyweb.db import Migration, Query, Schema, Transaction
from pyweb.models import Email, Model


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    Model.configure(str(tmp_path / "t.db"))
    yield
    Model.configure(":memory:")


def _m(**kw):
    class T(Model):
        name: str
        age: int
    T._table = kw.get("table", "t_items_xyz")
    return T


def test_create_all_where():
    T = _m()
    T.create(name="a", age=1)
    T.create(name="b", age=2)
    assert len(T.all()) == 2
    assert T.where(name="a")[0]["age"] == 1
    assert T.where() != []


def test_param_queries_not_interpolated():
    T = _m(table="t_items_inj")
    T.create(name="x", age=1)
    rows = T.where(name="' OR '1'='1")
    assert rows == []


def test_email_validate():
    assert Email.validate("a@b.com") == "a@b.com"
    with pytest.raises(ValueError):
        Email.validate("nope")


def test_table_names():
    class Product(Model):
        name: str
    assert Product.table() == "products"


def test_query_builder_sql():
    q = Query("products").where(stock__gt=0, price__lt=1000).order_by("price", desc=True).paginate(2, 10)
    sql, params = q.sql()
    assert "stock\" > ?" in sql and "LIMIT 10 OFFSET 10" in sql
    assert params == [0, 1000]


def test_query_no_where():
    sql, params = Query("t").sql()
    assert sql == 'SELECT * FROM "t"' and params == []


def test_transaction_commit_and_rollback(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "tx.db"))
    conn.execute("CREATE TABLE t (v TEXT)")
    with Transaction(conn):
        conn.execute("INSERT INTO t VALUES ('a')")
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    with pytest.raises(RuntimeError):
        with Transaction(conn):
            conn.execute("INSERT INTO t VALUES ('b')")
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1


def test_migrations_apply_once(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "m.db"))
    s = Schema()
    s.add(Migration("001", ["CREATE TABLE t (v TEXT)"]))
    assert s.migrate(conn) == ["001"]
    assert s.migrate(conn) == ["001"]
