"""The auth kit's tables on real SQLite, Postgres and MySQL: users, one-time tokens
(single use under a race), passkeys (bytes), JSON roles, the audit log and policies.

Enable with PYWEB_TEST_POSTGRES / PYWEB_TEST_MYSQL. The kit's tables have fixed
names, so they're dropped before and after each test.
"""

import datetime as dt
import os
import threading

import pytest

from pyweb import models as M
from pyweb.db import connect, request_scope
from pyweb.db import schema as S

URLS = {
    "sqlite": None,
    "postgres": os.environ.get("PYWEB_TEST_POSTGRES"),
    "mysql": os.environ.get("PYWEB_TEST_MYSQL"),
}
TABLES = ["auth_events", "auth_tokens", "auth_credentials", "auth_identities", "users"]


def _drop(database):
    existing = set(S.table_names(database))
    for name in TABLES:
        if name in existing:
            database.execute(f"DROP TABLE {database.dialect.quote(name)}")


@pytest.fixture(params=["sqlite", "postgres", "mysql"])
def db(request, tmp_path, monkeypatch):
    kind = request.param
    url = f"sqlite:///{tmp_path / 'a.db'}" if kind == "sqlite" else URLS[kind]
    if not url:
        pytest.skip(f"set PYWEB_TEST_{kind.upper()} to run against {kind}")
    database = connect(url)
    _drop(database)
    monkeypatch.setattr(M._state, "db", None)
    M.use_database(database)
    from pyweb.authkit.models import MODELS
    M.ensure_tables(database, MODELS)
    yield database
    _drop(database)
    M._state.db = None
    database.close()


def test_users_tokens_passkeys_and_events(db):
    from pyweb.authkit.models import AuthEvent, AuthToken, Credential, User
    from pyweb.authkit import passwords
    user = User.create(email="ada@example.com", name="Ada", roles=["admin", "staff"],
                       password_hash=passwords.hash_password("correct horse battery"))
    again = User.get(user.id)
    assert again.roles == ["admin", "staff"] and again.has_role("admin") and again.is_active
    assert again.created_at is not None
    assert passwords.verify_password("correct horse battery", again.password_hash)

    key = bytes(range(256)) * 2
    Credential.create(user=user, credential_id="cred-1", public_key=key, algorithm=-7)
    assert Credential.where(credential_id="cred-1").first().public_key == key

    token = AuthToken.issue("reset", user=user, ttl=900)
    assert AuthToken.redeem("reset", token).user_id == user.id
    assert AuthToken.redeem("reset", token) is None                 # used up
    assert AuthToken.redeem("magic", AuthToken.issue("reset", user=user)) is None   # wrong purpose
    assert AuthToken.redeem("reset", AuthToken.issue("reset", user=user, ttl=-5)) is None  # expired
    data_token = AuthToken.issue("signup", email="new@example.com", data={"name": "N", "x": [1, 2]})
    assert AuthToken.redeem("signup", data_token).data == {"name": "N", "x": [1, 2]}

    AuthEvent.create(user=user, kind="login", ip="203.0.113.9")
    AuthEvent.create(user=user, kind="login_failed", ok=False)
    assert [e.kind for e in AuthEvent.where(user=user)] == ["login_failed", "login"]   # newest first
    user.delete()
    assert AuthEvent.query().count() == 2 and AuthEvent.first().user_id is None       # kept, unlinked
    assert AuthToken.query().where(AuthToken.user_id == user.id).count() == 0           # cascaded


def test_a_link_clicked_twice_at_once_works_once(db):
    from pyweb.authkit.models import AuthToken, User
    user = User.create(email="race@example.com")
    for _ in range(5):
        token = AuthToken.issue("magic", user=user)
        wins, barrier = [], threading.Barrier(4)

        def click():
            barrier.wait()
            with request_scope():
                if AuthToken.redeem("magic", token) is not None:
                    wins.append(1)

        threads = [threading.Thread(target=click) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(wins) == 1


def test_session_versions_survive_in_the_users_table(db):
    from pyweb.authkit import _DBSessionVersions
    from pyweb.authkit.models import User
    user = User.create(email="v@example.com")
    versions = _DBSessionVersions(None)
    assert versions.get(str(user.id)) == 0
    versions.bump(str(user.id))
    versions.bump(str(user.id))
    assert versions.get(str(user.id)) == 2 and User.get(user.id).session_version == 2
    assert versions.get("999999") == 0


def test_last_login_round_trips_as_an_aware_time(db):
    from pyweb.authkit.models import User
    when = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)
    user = User.create(email="t@example.com")
    User.where(id=user.id).update(last_login_at=when)
    got = User.get(user.id).last_login_at
    assert got.replace(tzinfo=got.tzinfo or dt.timezone.utc) == when


def test_session_check_fails_closed_when_the_database_errors(db, monkeypatch):
    from pyweb.authkit import _DBSessionVersions
    from pyweb.authkit.models import User
    user = User.create(email="down@example.com")

    def broken(*a, **k):
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(User, "where", broken)
    assert _DBSessionVersions(None).get(user.id) > 10 ** 9          # every session counts as revoked
    assert _DBSessionVersions(None).get("not-a-number") == 0
