"""Property: for any schema and any change to it, diff -> upgrade leaves the database
exactly matching the Models, keeps existing data, and downgrade undoes everything."""

import datetime as dt
import itertools
import os
import tempfile

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings, strategies as st  # noqa: E402

from pyweb.db import SQLiteDB, migrate as mig, schema as S  # noqa: E402
from pyweb.models import Field, Model  # noqa: E402

TYPES = {"int": int, "str": str, "float": float, "bool": bool, "datetime": dt.datetime, "json": dict}
SAMPLE = {"int": 7, "str": "text", "float": 1.5, "bool": True,
          "datetime": dt.datetime(2026, 1, 2, 3, 4, tzinfo=dt.timezone.utc), "json": {"k": [1]}}
_ids = itertools.count()
EXAMPLES = int(os.environ.get("PYWEB_PROPERTY_EXAMPLES", "25"))

names = st.sampled_from(["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"])
column = st.tuples(names, st.sampled_from(sorted(TYPES)), st.booleans(), st.booleans())   # name, type, optional, unique
table = st.lists(column, min_size=1, max_size=4, unique_by=lambda c: c[0])
schema = st.lists(table, min_size=1, max_size=3)


def build(spec, tag, *, link=True):
    """Model classes for ``spec`` (a list of column lists); table i may point at table i-1."""
    classes = []
    for i, cols in enumerate(spec):
        anns, ns = {}, {}
        for name, kind, optional, unique in cols:
            typ = TYPES[kind]
            anns[name] = (typ | None) if optional else typ
            if unique and kind in ("int", "str") and not optional:
                ns[name] = Field(unique=True)
        if link and i > 0:
            anns["parent"] = classes[-1] | None
            ns["parent"] = None
        ns.update({"__annotations__": anns, "__module__": __name__,
                   "Meta": type("Meta", (), {"table": f"t{tag}_{i}"})})
        classes.append(type(f"P{tag}x{i}", (Model,), ns))
    return classes


def same(db, models):
    plan = S.diff(S.introspect(db), S.from_models(models), db.dialect)
    return not plan, [op.describe() for op in plan.ops]


@settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=list(HealthCheck))
@given(first=schema, data=st.data())
def test_any_schema_change_round_trips(first, data):
    tag = next(_ids)
    with tempfile.TemporaryDirectory() as tmp:
        db = SQLiteDB(os.path.join(tmp, "p.db"))
        d = os.path.join(tmp, "migrations")
        v1 = build(first, f"{tag}a")
        mig.make_migration(db, v1, d, allow_destructive=True)
        mig.upgrade(db, d, contract=True)
        ok, ops = same(db, v1)
        assert ok, ops

        # a row per table, so changes must keep data
        rows = []
        for model, cols in zip(v1, first):
            model.bind(db)
            values = {name: SAMPLE[kind] for name, kind, _, _ in cols}
            rows.append((model, model.create(**values).id, values))

        # the next version: keep some columns, add optional ones, maybe add a table
        second = []
        for cols in first:
            kept = [c for c in cols if data.draw(st.booleans(), label=f"keep {c[0]}")] or [cols[0]]
            loosened = [(n, k, opt or data.draw(st.booleans()), u) for n, k, opt, u in kept]
            extra = data.draw(st.lists(st.tuples(st.sampled_from(["iota", "kappa", "lambda_"]),
                                                 st.sampled_from(sorted(TYPES))), max_size=2, unique_by=lambda c: c[0]))
            second.append(loosened + [(n, k, True, False) for n, k in extra if n not in {c[0] for c in loosened}])
        if data.draw(st.booleans(), label="add table"):
            second.append(data.draw(table))
        v2 = build(second, f"{tag}a")                 # same table names: a real schema change
        mig.make_migration(db, v2, d, allow_destructive=True)
        mig.upgrade(db, d, contract=True)
        ok, ops = same(db, v2)
        assert ok, ops

        for (old_model, pk, values), cols, new_model in zip(rows, second, v2):
            new_model.bind(db)
            got = new_model.get(pk)
            assert got is not None
            for name, kind, _, _ in cols:
                if name in values:
                    assert getattr(got, name) == values[name], (name, kind)

        mig.downgrade(db, d, steps=10)
        assert S.introspect(db) == {}
