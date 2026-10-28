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


def test_the_policy_fails_closed(app_db):
    """No setting, an empty one, or a view the statement doesn't name: no rows from another organization."""
    db, admin = app_db
    from pyweb.authkit.orgs import Organization
    Box = make_model()
    a = Organization.create(name="Acme", slug="acme").id
    b = Organization.create(name="Globex", slug="globex").id
    with tenancy.org_scope(a):
        Box.create(label="a1")
    with tenancy.org_scope(b):
        Box.create(label="b1")
    admin.execute("CREATE OR REPLACE VIEW rls_box_view WITH (security_invoker = true) AS SELECT label FROM rls_boxes")
    admin.execute(f"GRANT SELECT ON rls_box_view TO {ROLE}")
    try:
        with tenancy.org_scope(a):
            # the statement names only the view: the organization is set anyway
            assert [r[0] for r in db.execute("SELECT label FROM rls_box_view").fetchall()] == ["a1"]
            # SQL that blanks the setting doesn't widen the next statement: PyWeb sets it again
            with db.transaction():
                db.execute("SELECT set_config('pyweb.org_id', '', true)")
                assert db.execute("SELECT count(*) FROM rls_box_view").fetchone()[0] == 1
        # A connection that never set pyweb.org_id (a console, another tool) sees nothing (it used to
        # see every organization).
        import psycopg
        parts = urllib.parse.urlparse(URL)
        raw = psycopg.connect(parts._replace(netloc=f"{ROLE}:{PASSWORD}@{parts.hostname}:{parts.port or 5432}").geturl())
        try:
            assert raw.execute("SELECT count(*) FROM rls_boxes").fetchone()[0] == 0
            raw.execute("SELECT set_config('pyweb.org_id', '', false)")
            assert raw.execute("SELECT count(*) FROM rls_boxes").fetchone()[0] == 0
        finally:
            raw.close()
    finally:
        admin.execute("DROP VIEW IF EXISTS rls_box_view")


def test_old_policies_are_replaced(app_db):
    db, admin = app_db
    from pyweb.authkit.orgs import Organization
    Box = make_model()
    with tenancy.org_scope(Organization.create(name="Acme", slug="acme").id):
        Box.create(label="a1")                        # creates the table (and its new policy)
    admin.execute("DROP POLICY IF EXISTS pyweb_tenant_v2 ON rls_boxes")
    admin.execute("CREATE POLICY pyweb_tenant ON rls_boxes USING (true)")
    from pyweb.db import rls
    assert not rls.protected(admin, "rls_boxes")
    assert rls.ensure(admin, [make_model()]) == ["rls_boxes"]
    names = [r[0] for r in admin.execute("SELECT policyname FROM pg_policies WHERE tablename = 'rls_boxes'").fetchall()]
    assert names == ["pyweb_tenant_v2"]
