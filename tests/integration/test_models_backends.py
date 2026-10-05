"""Models and migrations against real SQLite, Postgres and MySQL.

Enable with PYWEB_TEST_POSTGRES / PYWEB_TEST_MYSQL (see test_services.py).
Every test uses tables with a unique prefix and drops them afterwards.
"""

import datetime as dt
import decimal
import itertools
import os
import threading
import uuid

import pytest

from pyweb import models as M
from pyweb.db import connect, migrate as mig, request_scope, schema as S
from pyweb.models import Count, Field, ForeignKey, Model, Sum, ValidationError

URLS = {
    "sqlite": None,
    "postgres": os.environ.get("PYWEB_TEST_POSTGRES"),
    "mysql": os.environ.get("PYWEB_TEST_MYSQL"),
}
_n = itertools.count()


@pytest.fixture(params=["sqlite", "postgres", "mysql"])
def db(request, tmp_path, monkeypatch):
    kind = request.param
    if kind == "sqlite":
        url = f"sqlite:///{tmp_path / 'm.db'}"
    else:
        url = URLS[kind]
        if not url:
            pytest.skip(f"set PYWEB_TEST_{kind.upper()} to run against {kind}")
    database = connect(url)
    monkeypatch.setattr(M._state, "db", None)
    M.use_database(database)
    tag = f"x{uuid.uuid4().hex[:6]}"
    database.tag = tag
    yield database
    for _ in range(3):                         # drop children before parents
        for name in sorted(S.table_names(database), reverse=True):
            if name.startswith(tag):
                try:
                    database.execute(f"DROP TABLE {database.dialect.quote(name)}")
                except Exception:  # noqa: BLE001 - a parent still referenced; next round
                    pass
    M._state.db = None
    database.close()


def shop(tag):
    class Customer(Model):
        email: str = Field(unique=True, max=120)
        name: str = Field(max=60)

        class Meta:
            table = f"{tag}_customers"

    class Product(Model):
        name: str = Field(max=80)
        price: decimal.Decimal = Field(decimal.Decimal("0"), precision=10, scale=2)

        class Meta:
            table = f"{tag}_products"

    class Order(Model):
        customer: Customer
        products: list[Product] = []
        note: str | None = None
        total: int = 0
        placed_at: dt.datetime | None = None
        extra: dict | None = None
        paid: bool = False

        class Meta:
            table = f"{tag}_orders"
            ordering = ["id"]
    return Customer, Product, Order


def migrate_to(db, models, tmp_path, **kw):
    d = str(tmp_path / f"migrations_{db.tag}")
    kw.setdefault("name", f"{db.tag}_initial")         # the journal is shared by every test run
    paths, plan = mig.make_migration(db, models, d, **kw)
    mig.upgrade(db, d, contract=True)
    return d, paths, plan


def test_models_round_trip_and_relations(db, tmp_path):
    Customer, Product, Order = shop(db.tag)
    migrate_to(db, [Customer, Product, Order], tmp_path)
    assert not S.diff(S.introspect(db), S.from_models([Customer, Product, Order]), db.dialect)
    ada = Customer.create(email="ada@example.com", name="Ada")
    pen, ink = Product.create(name="Pen", price="2.50"), Product.create(name="Ink", price=decimal.Decimal("7.25"))
    when = dt.datetime(2026, 3, 4, 5, 6, 7, tzinfo=dt.timezone.utc)
    o1 = Order.create(customer=ada, products=[pen, ink], total=975, placed_at=when, extra={"gift": True}, paid=True)
    Order.create(customer=ada, products=[pen], total=250)
    got = Order.where(id=o1.id).include("customer", "products").first()
    assert got.customer.name == "Ada" and sorted(p.name for p in got.products) == ["Ink", "Pen"]
    assert got.placed_at == when and got.extra == {"gift": True} and got.paid is True
    assert Product.get(ink.id).price == decimal.Decimal("7.25")
    assert Order.query().aggregate(n=Count(), total=Sum("total")) == {"n": 2, "total": 1225}
    assert Order.query().group_by("customer").values("customer", n=Count()) == [{"customer": ada.id, "n": 2}]
    assert Customer.where(Customer.orders.any(Order.total > 500)).count() == 1
    assert Order.where(Order.products.any(Product.name == "Ink")).count() == 1
    assert Order.where(note__isnull=True, total__gte=250).count() == 2
    assert Product.where(name__icontains="PE").count() == 1
    with pytest.raises(ValidationError) as err:
        Customer.create(email="ada@example.com", name="Other")
    assert err.value.errors == {"email": "is already taken"}
    first = Order.query().page(size=1)
    second = Order.query().page(size=1, after=first.next)
    assert [o.id for o in first] == [o1.id] and len(second) == 1 and second.next is None
    Customer.upsert(key="email", email="ada@example.com", name="Ada L.")
    assert Customer.get(ada.id).name == "Ada L." and Customer.count() == 1
    ada.delete()
    assert Order.count() == 0                                  # cascade


def test_transactions_and_savepoints(db, tmp_path):
    Customer, Product, Order = shop(db.tag)
    migrate_to(db, [Customer, Product, Order], tmp_path)
    with db.transaction():
        Customer.create(email="a@example.com", name="A")
        with pytest.raises(ValidationError):
            with db.transaction():
                Customer.create(email="b@example.com", name="B")
                Customer.create(email="a@example.com", name="dup")
    assert sorted(Customer.query().pluck("email")) == ["a@example.com"]
    with pytest.raises(RuntimeError):
        with request_scope():
            Customer.create(email="c@example.com", name="C")
            raise RuntimeError("undo")
    assert Customer.count() == 1


def test_schema_changes(db, tmp_path):
    tag = db.tag
    Customer, Product, Order = shop(tag)
    d, _, _ = migrate_to(db, [Customer, Product, Order], tmp_path)
    ada = Customer.create(email="ada@example.com", name="Ada")
    Order.create(customer=ada, total=5, note="hello")

    class Order(Model):                                        # noqa: F811 - the next version
        customer: Customer
        products: list[Product] = []
        memo: str | None = None                                # renamed from note
        total: float = 0.0                                     # int -> float
        channel: str = Field(max=20)                           # new and required
        paid: bool = False
        coupon: Customer | None = ForeignKey(related_name="coupon_orders", on_delete="set null")

        class Meta:
            table = f"{tag}_orders"
    paths, plan = mig.make_migration(db, [Customer, Product, Order], d, f"{tag}_change",
                                     renames={f"{tag}_orders.note": "memo"})
    assert len(paths) == 2
    mig.upgrade(db, d)                                         # expand only
    live = S.introspect(db)[f"{tag}_orders"]
    assert live.column("note") is not None and live.column("memo") is not None
    db.execute(f"UPDATE {db.dialect.quote(tag + '_orders')} SET {db.dialect.quote('channel')} = ?", ("web",))
    mig.upgrade(db, d, contract=True)
    live = S.introspect(db)[f"{tag}_orders"]
    assert live.column("note") is None and live.column("placed_at") is None
    assert not live.column("channel").nullable
    assert not S.diff(S.introspect(db), S.from_models([Customer, Product, Order]), db.dialect)
    row = Order.query().first()
    assert row.memo == "hello" and row.total == 5.0 and row.channel == "web"
    mig.downgrade(db, d, steps=1)
    assert S.introspect(db)[f"{tag}_orders"].column("note") is not None


def test_concurrent_upgrades_apply_once(db, tmp_path):
    if db.dialect.name == "sqlite":
        url = f"sqlite:///{tmp_path / 'm.db'}"
    else:
        url = URLS[db.dialect.name]
    tag = db.tag
    mig._applied(db)
    db.execute("DELETE FROM pyweb_migrations WHERE version = ?", ("0001_slow",))
    d = tmp_path / "m"
    d.mkdir()
    (d / "0001_slow.py").write_text(
        "import time\n\ndef up(op):\n    time.sleep(0.3)\n"
        f"    op.create_table('{tag}_things', [op.column('id', 'bigint', nullable=False, primary_key=True, autoincrement=True)])\n\n"
        f"def down(op):\n    op.drop_table('{tag}_things')\n")
    results, errors = [], []

    def run():
        try:
            results.append(mig.upgrade(connect(url), str(d)))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert sorted(map(len, results)) == [0, 0, 1]
    mig.downgrade(db, str(d))
    db.execute("DELETE FROM pyweb_migrations WHERE version = ?", ("0001_slow",))
