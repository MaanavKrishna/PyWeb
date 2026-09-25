"""Auth: hashing, sessions, CSRF, RBAC/permissions."""

import time

from pyweb import auth
from pyweb.auth import (
    SessionStore,
    can,
    csrf_token,
    hash_password,
    permission,
    required,
    verify_csrf,
    verify_password,
)


def test_password_roundtrip_and_wrong():
    h = hash_password("s3cret")
    assert verify_password("s3cret", h) and not verify_password("nope", h)
    assert not verify_password("x", "garbage")


def test_session_sign_tamper_expiry():
    store = SessionStore("s3cr3t", ttl=1000)
    tok = store.create("u1", {"role": "admin"})
    assert store.get(tok)["user_id"] == "u1"
    assert store.get(tok[:-2] + "xx") is None
    assert store.get("bad") is None
    store.destroy(tok)
    assert store.get(tok) is None


def test_session_expiry():
    store = SessionStore("s", ttl=-1)
    tok = store.create("u")
    assert store.get(tok) is None


def test_csrf_roundtrip():
    t = csrf_token("k", "sess1")
    assert verify_csrf("k", "sess1", t) and not verify_csrf("k", "sess1", "bad")


def test_required_and_permissions():
    @required
    def dash():
        pass
    assert dash.__pyweb_auth__ == "required"

    @permission("admin")
    @permission("billing")
    def delete():
        pass
    assert can(["admin", "billing"], delete)
    assert not can(["admin"], delete)
    assert can([], lambda: None)


def test_password_hash_differs_per_salt():
    assert hash_password("x") != hash_password("x")
