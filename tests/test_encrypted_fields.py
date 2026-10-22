"""Field(encrypted=True): AES-256-GCM at rest, blind indexes, key rotation, and what stays refused."""

import os

import pytest

from pyweb import fieldcrypt
from pyweb import models as M
from pyweb.db import SQLiteDB
from pyweb.models import Field, Model, Text

pytest.importorskip("cryptography")


class Patient(Model):
    name: str
    phone: str = Field(encrypted=True, searchable=True)
    notes: Text | None = Field(None, encrypted=True)
    chart: dict = Field(default_factory=dict, encrypted=True)
    scan: bytes | None = Field(None, encrypted=True)


@pytest.fixture(autouse=True)
def db(monkeypatch):
    monkeypatch.setenv("PYWEB_DATA_KEY", "k1-" + "x" * 40)
    monkeypatch.delenv("PYWEB_DATA_KEY_PREVIOUS", raising=False)
    database = SQLiteDB(":memory:")
    monkeypatch.setattr(M._state, "db", None)
    M.use_database(database, auto_create=True)
    yield database
    M._state.db = None
    database.close()


def raw(db, column, pk):
    return db.execute(f"SELECT {column} FROM patients WHERE id = ?", [pk]).fetchone()[0]


def test_values_are_encrypted_at_rest_and_read_back(db):
    p = Patient.create(name="Ann", phone="555-0100", notes="allergic to penicillin", chart={"bp": [120, 80]},
                       scan=b"\x00\x01")
    for column in ("phone", "notes", "chart", "scan"):
        stored = raw(db, column, p.id)
        assert fieldcrypt.is_ciphertext(stored), column
    assert "555" not in raw(db, "phone", p.id) and "penicillin" not in raw(db, "notes", p.id)
    again = Patient.get(p.id)
    assert (again.phone, again.notes, again.chart, again.scan) == ("555-0100", "allergic to penicillin",
                                                                   {"bp": [120, 80]}, b"\x00\x01")
    twin = Patient.create(name="Bo", phone="555-0100")
    assert raw(db, "phone", p.id) != raw(db, "phone", twin.id)          # same value, different ciphertext


def test_searchable_fields_match_exact_values_only(db):
    Patient.create(name="Ann", phone="555-0100")
    Patient.create(name="Bo", phone="555-0199")
    assert Patient.where(phone="555-0100").first().name == "Ann"
    assert [p.name for p in Patient.where(Patient.phone.in_(["555-0199", "nope"]))] == ["Bo"]
    assert Patient.where(Patient.phone != "555-0100").count() == 1
    assert Patient.where(phone="555-01").count() == 0
    for bad in (lambda: Patient.where(Patient.phone > "5").count(),
                lambda: Patient.where(Patient.phone.like("555%")).count(),
                lambda: Patient.query().order("phone").first(),
                lambda: Patient.where(Patient.notes == "x").count()):
        with pytest.raises(ValueError, match="encrypted"):
            bad()


def test_encrypted_values_stay_out_of_to_dict():
    p = Patient.create(name="Ann", phone="555-0100")
    assert p.to_dict() == {"id": p.id, "name": "Ann"}
    assert p.to_dict(include_private=True)["phone"] == "555-0100"


def test_a_ciphertext_moved_to_another_column_or_row_tamper_fails(db):
    p = Patient.create(name="Ann", phone="555-0100", notes="n")
    db.execute("UPDATE patients SET notes = phone WHERE id = ?", [p.id])
    with pytest.raises(fieldcrypt.DecryptError, match="another column"):
        Patient.get(p.id)


def test_rotation_reads_old_rows_and_reencrypt_rewrites_them(db, monkeypatch):
    p = Patient.create(name="Ann", phone="555-0100", chart={"a": 1})
    old_kid = raw(db, "phone", p.id).split(":")[1]
    monkeypatch.setenv("PYWEB_DATA_KEY", "k2-" + "y" * 40)
    with pytest.raises(fieldcrypt.DecryptError, match="isn't configured"):
        Patient.get(p.id)
    monkeypatch.setenv("PYWEB_DATA_KEY_PREVIOUS", "k1-" + "x" * 40)
    assert Patient.get(p.id).phone == "555-0100"
    assert Patient.where(phone="555-0100").count() == 1          # old blind index still found
    assert fieldcrypt.reencrypt(db, [Patient]) == {"patients": 1}
    assert raw(db, "phone", p.id).split(":")[1] != old_kid
    assert fieldcrypt.reencrypt(db, [Patient]) == {"patients": 0}
    monkeypatch.delenv("PYWEB_DATA_KEY_PREVIOUS")
    again = Patient.where(phone="555-0100").first()
    assert again is not None and again.chart == {"a": 1}


def test_production_needs_a_data_key(monkeypatch):
    monkeypatch.delenv("PYWEB_DATA_KEY")
    monkeypatch.setenv("PYWEB_ENV", "production")
    with pytest.raises(RuntimeError, match="PYWEB_DATA_KEY"):
        Patient.create(name="Ann", phone="1")


def test_fields_that_cant_be_encrypted_say_so():
    with pytest.raises(TypeError, match="only str"):
        class A(Model):
            n: int = Field(0, encrypted=True)
        A._meta.fields  # noqa: B018
    with pytest.raises(TypeError, match="unique"):
        class B(Model):
            s: str = Field(encrypted=True, unique=True)
        B._meta.fields  # noqa: B018


def test_schema_has_text_columns_and_an_indexed_blind_index():
    from pyweb.db.schema import from_models
    table = from_models([Patient])["patients"]
    cols = {c.name: c for c in table.columns}
    assert cols["phone"].kind == "text" and cols["phone_bidx"].kind == "str"
    assert any(i.columns == ("phone_bidx",) for i in table.indexes)
    assert "notes_bidx" not in cols


def test_production_check_flags_a_missing_or_shared_key(monkeypatch):
    from pyweb.cli import production_problems
    src = "phone: str = Field(encrypted=True)"
    monkeypatch.delenv("PYWEB_DATA_KEY")
    assert any("PYWEB_DATA_KEY isn't set" in p for p in production_problems(src))
    monkeypatch.setenv("PYWEB_DATA_KEY", "s" * 40)
    monkeypatch.setenv("PYWEB_AUTH_SECRET", "s" * 40)
    assert any("same as PYWEB_AUTH_SECRET" in p for p in production_problems(src))


@pytest.mark.parametrize("env", ["PYWEB_TEST_POSTGRES", "PYWEB_TEST_MYSQL"])
def test_other_databases(env, monkeypatch):
    url = os.environ.get(env)
    if not url:
        pytest.skip(f"{env} not set")
    from pyweb.db import connect
    database = connect(url)
    database.execute("DROP TABLE IF EXISTS patients")
    monkeypatch.setattr(M._state, "db", None)
    M.use_database(database, auto_create=True)
    try:
        p = Patient.create(name="Ann", phone="555-0100", chart={"k": [1]}, scan=b"\xff")
        assert fieldcrypt.is_ciphertext(raw(database, "phone", p.id))
        got = Patient.where(phone="555-0100").first()
        assert (got.chart, got.scan) == ({"k": [1]}, b"\xff")
    finally:
        database.execute("DROP TABLE IF EXISTS patients")
