"""Uploaded files no row refers to are deleted after a day; referenced ones never."""

import datetime as dt

import pytest

from pyweb import filegc, forms, jobs, storage
from pyweb import models as M
from pyweb.db import SQLiteDB
from pyweb.models import File, Model, system


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PYWEB_STORAGE", f"file://{tmp_path}/uploads")
    monkeypatch.setattr(storage, "_current", None)
    db = SQLiteDB(":memory:")
    monkeypatch.setattr(M._state, "db", None)
    M.use_database(db, auto_create=True)
    filegc._ready.clear()

    class Avatar(Model):
        name: str
        photo: str | None = File(types=["image/*"])

    yield Avatar
    M._state.db = None
    db.close()


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def upload(name):
    return forms.UploadedFile(name, PNG, "image/png").save()


def test_unreferenced_files_go_after_the_grace_period(env):
    Avatar = env
    kept, replaced, abandoned = upload("a.png"), upload("b.png"), upload("c.png")
    a = Avatar.create(name="ann", photo=replaced)
    a.photo = kept
    a.save()                                                  # replaced: b.png is now unreferenced
    store = storage.storage()
    assert filegc.collect([Avatar]) == []                     # everything is younger than a day
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)
    assert sorted(filegc.collect([Avatar], now=later, dry_run=True)) == sorted([replaced, abandoned])
    assert store.exists(replaced)                             # a dry run deletes nothing
    assert sorted(filegc.collect([Avatar], now=later)) == sorted([replaced, abandoned])
    assert store.exists(kept) and not store.exists(replaced) and not store.exists(abandoned)
    with system():
        assert [r.key for r in filegc.StoredFile.query()] == [kept]
    assert filegc.collect([Avatar], now=later) == []


def test_apps_with_file_fields_get_a_daily_collection(env):
    Avatar = env
    jobs.REGISTRY.pop("pyweb.files.collect", None)
    assert filegc.schedule([Avatar])
    assert "pyweb.files.collect" in jobs.REGISTRY
    assert "pyweb.files.collect" in jobs.SCHEDULES

    class Plain(Model):
        name: str
    assert filegc.schedule([Plain]) is False
