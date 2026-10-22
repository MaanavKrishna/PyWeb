"""Deleting uploaded files nothing refers to any more.

Every file saved through a form (``File`` fields, ``<FileInput>``) is recorded
in PyWeb's ``pyweb_files`` table. Once a day (and on ``pyweb files clean``)
PyWeb collects every key still held by a ``File`` field of any Model, and
deletes recorded files that no row refers to and that are more than a day
old: an avatar that was replaced, the attachment of a deleted message, or an
upload whose form never saved. The day of grace covers forms that upload first
and save a moment later. Works the same for a local folder and for S3.
"""

from __future__ import annotations

import datetime as dt
import logging

from pyweb.models import Field, Model

log = logging.getLogger("pyweb.files")
GRACE = 24 * 3600


class StoredFile(Model):
    key: str = Field(unique=True, max=200)
    size: int = 0
    content_type: str | None = Field(None, max=120)
    created_at: dt.datetime = Field(auto_now_add=True)

    class Meta:
        table = "pyweb_files"


_ready: set = set()


def _db():
    from pyweb import models as M
    db = M.database() or StoredFile._database()
    if id(db) not in _ready:
        M.ensure_tables(db, [StoredFile])
        _ready.add(id(db))
    return db


def track(key, size=0, content_type=None):
    """Record a stored file (best effort: an app without a database just isn't collected)."""
    from pyweb.models import system
    try:
        _db()
        with system():
            StoredFile.create(key=key, size=int(size or 0), content_type=(content_type or None) and content_type[:120])
    except Exception:  # noqa: BLE001 - recording must never break an upload
        log.debug("couldn't record stored file %s", key, exc_info=True)


def file_fields(models=None):
    """``[(model, field)]`` for every ``File`` field."""
    from pyweb.models import all_models
    out = []
    for model in models if models is not None else all_models():
        for f in model._meta.fields.values():
            if getattr(f, "is_file", False):
                out.append((model, f))
    return out


def referenced(models=None):
    """Every storage key some row still holds."""
    from pyweb.models import system
    keys = set()
    with system():
        for model, f in file_fields(models):
            for row in model.query().unscoped().values(f.name):
                if row.get(f.name):
                    keys.add(row[f.name])
    return keys


def collect(models=None, *, grace=GRACE, now=None, dry_run=False):
    """Delete recorded files no row refers to and older than ``grace`` seconds; returns their keys."""
    from pyweb import storage
    from pyweb.models import system
    _db()
    now = now or dt.datetime.now(dt.timezone.utc)
    keep = referenced(models)
    gone = []
    with system():
        for row in StoredFile.where(StoredFile.created_at < now - dt.timedelta(seconds=grace)).order("id"):
            if row.key in keep:
                continue
            gone.append(row.key)
            if not dry_run:
                try:
                    storage.storage().delete(row.key)
                except Exception:  # noqa: BLE001 - try again tomorrow
                    log.warning("couldn't delete stored file %s", row.key, exc_info=True)
                    gone.pop()
                    continue
                StoredFile.where(id=row.id).delete()
    if gone and not dry_run:
        log.info("deleted %d unreferenced file(s)", len(gone))
    return gone


def schedule(models):
    """Collect daily when the app has ``File`` fields (called by the app loader)."""
    if not file_fields(models):
        return False
    from pyweb import jobs
    if "pyweb.files.collect" not in jobs.REGISTRY:
        jobs.cron("17 3 * * *", name="pyweb.files.collect")(_collect_all)
    return True


def _collect_all():
    return len(collect())
