"""Postgres row-level security for tenant Models: raw SQL can't cross organizations either."""

import os
import urllib.parse

import pytest

from pyweb import models as M
from pyweb import tenancy

URL = os.environ.get("PYWEB_TEST_POSTGRES")
pytestmark = pytest.mark.skipif(not URL, reason="PYWEB_TEST_POSTGRES not set")

ROLE, PASSWORD = "pyweb_rls_app", "rls-test-password"


@pytest.fixture
def app_db(monkeypatch):
    from pyweb.db import connect
    admin = connect(URL)                              # a superuser: row-level security doesn't apply to it
    exists = admin.execute("SELECT 1 FROM pg_roles WHERE rolname = ?", (ROLE,)).fetchone()
    if not exists:
        admin.execute(f"CREATE ROLE {ROLE} LOGIN PASSWORD '{PASSWORD}'")
    dbname = urllib.parse.urlparse(URL).path.lstrip("/")
    admin.execute(f'GRANT ALL ON DATABASE "{dbname}" TO {ROLE}')
    admin.execute(f"GRANT ALL ON SCHEMA public TO {ROLE}")
    for t in ("rls_boxes", "org_invitations", "org_memberships", "organizations", "pyweb_migrations"):
        admin.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
    parts = urllib.parse.urlparse(URL)
    app_url = parts._replace(netloc=f"{ROLE}:{PASSWORD}@{parts.hostname}:{parts.port or 5432}").geturl()
    db = connect(app_url)
    monkeypatch.setattr(M._state, "db", None)
    M.use_database(db, auto_create=True)
    yield db, admin
    M._state.db = None
    db.close()
    for t in ("rls_boxes", "org_invitations", "org_memberships", "organizations", "pyweb_migrations"):
        admin.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
    admin.close()


def make_model():
    from pyweb.models import Field, Model

    class RlsBox(Model, tenant=True):
        label: str = Field("", max=20)

        class Meta:
            table = "rls_boxes"
    return RlsBox


def test_raw_sql_is_limited_by_the_database(app_db):
    db, admin = app_db
    from pyweb.authkit.orgs import Organization
    Box = make_model()
    a = Organization.create(name="Acme", slug="acme").id
    b = Organization.create(name="Globex", slug="globex").id
    with tenancy.org_scope(a):
        Box.create(label="a1")
        Box.create(label="a2")
    with tenancy.org_scope(b):
        Box.create(label="b1")
    from pyweb.db import rls
    assert rls.protected(admin, "rls_boxes")

    # Raw SQL that forgets the organization: Postgres itself filters it.
    with tenancy.org_scope(a):
        rows = db.execute("SELECT label FROM rls_boxes ORDER BY label").fetchall()
        assert [r[0] for r in rows] == ["a1", "a2"]
        with pytest.raises(Exception):                # WITH CHECK: can't write into another organization
            db.execute("INSERT INTO rls_boxes (label, org_id) VALUES (?, ?)", ("x", b))
        assert db.execute("UPDATE rls_boxes SET label = 'hacked' WHERE label = 'b1'").rowcount == 0
    with tenancy.org_scope(None):
        assert db.execute("SELECT count(*) FROM rls_boxes").fetchone()[0] == 0
    # Outside any organization (migrations, scripts) everything is visible.
    assert db.execute("SELECT count(*) FROM rls_boxes").fetchone()[0] == 3
    with db.transaction():
        with tenancy.org_scope(b):
            assert [r[0] for r in db.execute("SELECT label FROM rls_boxes").fetchall()] == ["b1"]
        assert db.execute("SELECT count(*) FROM rls_boxes").fetchone()[0] == 3


def test_diff_adds_row_level_security_to_the_migration(app_db, tmp_path):
    db, admin = app_db
    from pyweb.authkit.orgs import Organization
    from pyweb.db import migrate
    Box = make_model()
    for t in ("rls_boxes", "org_invitations", "org_memberships", "organizations", "pyweb_migrations"):
        admin.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
    M._state.db = None
    M.use_database(db)                                 # no auto-create: migrations do it
    paths, plan = migrate.make_migration(db, [Box, Organization], str(tmp_path / "migrations"))
    text = open(paths[0]).read()
    assert "ENABLE ROW LEVEL SECURITY" in text and "FORCE ROW LEVEL SECURITY" in text
    migrate.upgrade(db, str(tmp_path / "migrations"))
    from pyweb.db import rls
    assert rls.protected(admin, "rls_boxes")
    paths, plan = migrate.make_migration(db, [Box, Organization], str(tmp_path / "migrations"))
    assert paths == []                                  # nothing left to do
