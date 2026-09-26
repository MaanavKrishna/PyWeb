"""Auth v1: Policy RBAC, session rotation, WebAuthn assertion verify."""

import hashlib
import json

import pytest

from pyweb import auth as _a


def _sess(sub="u1", roles=()):
    return {"sub": sub, "roles": list(roles)}


def test_policy_allows_role_without_resource():
    p = _a.Policy("admin-panel").allow("admin")
    assert p.check(_sess(roles=["admin"])) is True
    with pytest.raises(_a.Forbidden):
        p.check(_sess(roles=["user"]))
    with pytest.raises(_a.NotAuthenticated):
        p.check({})


def test_policy_owner_rule():
    p = _a.Policy("post:edit").allow("admin").allow("author",
                                                    owner_field="author_id")
    assert p.check(_sess("a", ["author"]),
                   {"author_id": "a"}) is True  # own post
    with pytest.raises(_a.Forbidden):
        p.check(_sess("b", ["author"]), {"author_id": "a"})  # other's
    assert p.check(_sess("root", ["admin"]),
                   {"author_id": "a"}) is True  # admin override


def test_policy_as_decorator():
    p = _a.Policy("doc:read").allow("reader")

    @p
    def read_doc(session, resource):
        return resource["title"]

    assert read_doc(session=_sess(roles=["reader"]),
                    resource={"title": "hi"}) == "hi"
    with pytest.raises(_a.Forbidden):
        read_doc(session=_sess(roles=[]), resource={"title": "hi"})
    assert "policy:doc:read" in read_doc.__pyweb_permissions__


def test_session_rotation_bounds_lifetime():
    store = _a.SessionStore("s", ttl=3600)
    old = store.create("u1", extra={"roles": ["user"]})
    new = _a.rotate_session(store, old)
    assert new and new != old
    assert store.get(old) is None  # old token dead
    sess = store.get(new)
    assert sess["user_id"] == "u1" and sess["roles"] == ["user"]
    assert _a.rotate_session(store, "bogus") is None


def _p256_sign(priv, msg):
    """Deterministic-ish ECDSA sign for tests (uses P-256 arithmetic)."""
    import secrets as _s
    n = _a._P256_N
    e = int.from_bytes(hashlib.sha256(msg).digest(), "big")
    gx, gy = _a._P256_GX, _a._P256_GY
    while True:
        k = int.from_bytes(_s.token_bytes(32), "big") % (n - 1) + 1
        pt = _a._p256_scalar_mult(k, (gx, gy))
        r = pt[0] % n
        if r == 0:
            continue
        s = (pow(k, -1, n) * (e + r * priv)) % n
        if s == 0:
            continue
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def test_webauthn_assertion_roundtrip():
    priv = 0xC9AFD9D7724B5E77B4E9D9E9F1A2B3C4D5E6F708192A3B4C5D6E7F8091A2B3
    pub = _a._p256_scalar_mult(priv, (_a._P256_GX, _a._P256_GY))
    raw_point = (b"\x04" + pub[0].to_bytes(32, "big")
                 + pub[1].to_bytes(32, "big"))
    rp_id = "example.com"
    auth_data = (hashlib.sha256(rp_id.encode()).digest() + b"\x01\x00\x00\x00"
                 + b"\x00" * 2)
    client = json.dumps({"type": "webauthn.get",
                         "challenge": "abc",
                         "origin": f"https://{rp_id}"}).encode()
    sig = _p256_sign(priv, auth_data + hashlib.sha256(client).digest())
    parsed = _a.verify_webauthn_assertion(
        credential_public_key=raw_point, auth_data=auth_data,
        client_data_json=client, signature=sig, rp_id=rp_id)
    assert parsed["user_present"] and parsed["sign_count"] == 0
    with pytest.raises(_a.AuthError):
        _a.verify_webauthn_assertion(
            credential_public_key=raw_point, auth_data=auth_data,
            client_data_json=client,
            signature=b"\x00" * 64, rp_id=rp_id)


def test_webauthn_rejects_wrong_rp_and_ceremony():
    priv = 0x1234
    pub = _a._p256_scalar_mult(priv, (_a._P256_GX, _a._P256_GY))
    raw = b"\x04" + pub[0].to_bytes(32, "big") + pub[1].to_bytes(32, "big")
    good_ad = hashlib.sha256(b"example.com").digest() + b"\x01\x00\x00\x00\x00"
    good_cd = json.dumps({"type": "webauthn.get"}).encode()
    sig = _p256_sign(priv, good_ad + hashlib.sha256(good_cd).digest())
    with pytest.raises(_a.AuthError):  # wrong rp
        _a.verify_webauthn_assertion(
            credential_public_key=raw, auth_data=good_ad,
            client_data_json=good_cd, signature=sig, rp_id="evil.com")
    bad_cd = json.dumps({"type": "webauthn.create"}).encode()
    with pytest.raises(_a.AuthError):  # wrong ceremony
        _a.verify_webauthn_assertion(
            credential_public_key=raw, auth_data=good_ad,
            client_data_json=bad_cd, signature=sig, rp_id="example.com")


def test_cose_to_raw_point():
    x = bytes(range(1, 33))
    y = bytes(range(33, 65))
    # COSE_Key {1: 2 (EC2), 3: -7 (ES256), -1: 1 (P-256), -2: x, -3: y}
    cose = (b"\xa5\x01\x02\x03\x26\x20\x01\x21\x58\x20" + x
            + b"\x22\x58\x20" + y)
    assert _a.cose_to_raw_point(cose) == b"\x04" + x + y
    with pytest.raises(ValueError):
        _a.cose_to_raw_point(b"\x00")


def test_parse_auth_data_flags():
    ad = hashlib.sha256(b"x").digest() + bytes([0x05]) + b"\x00\x00\x00\x07"
    p = _a.parse_auth_data(ad)
    assert p["user_present"] and p["user_verified"] and p["sign_count"] == 7
    with pytest.raises(ValueError):
        _a.parse_auth_data(b"short")
