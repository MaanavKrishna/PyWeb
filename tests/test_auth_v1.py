"""Auth v1: Policy RBAC, session rotation, WebAuthn assertion verify."""

import base64
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


def _assertion(priv, rp_id="example.com", challenge=b"server-challenge-1", origin=None,
               count=0, ctype="webauthn.get"):
    pub = _a._p256_scalar_mult(priv, (_a._P256_GX, _a._P256_GY))
    raw_point = b"\x04" + pub[0].to_bytes(32, "big") + pub[1].to_bytes(32, "big")
    auth_data = hashlib.sha256(rp_id.encode()).digest() + b"\x01" + count.to_bytes(4, "big")
    client = json.dumps({"type": ctype,
                         "challenge": base64.urlsafe_b64encode(challenge).rstrip(b"=").decode(),
                         "origin": origin or f"https://{rp_id}"}).encode()
    sig = _p256_sign(priv, auth_data + hashlib.sha256(client).digest())
    return raw_point, auth_data, client, sig


def _verify(raw, ad, cd, sig, **kw):
    args = dict(credential_public_key=raw, auth_data=ad, client_data_json=cd, signature=sig,
                rp_id="example.com", expected_challenge=b"server-challenge-1",
                expected_origin="https://example.com")
    args.update(kw)
    return _a.verify_webauthn_assertion(**args)


def test_webauthn_assertion_roundtrip():
    priv = 0xC9AFD9D7724B5E77B4E9D9E9F1A2B3C4D5E6F708192A3B4C5D6E7F8091A2B3
    raw, ad, cd, sig = _assertion(priv)
    parsed = _verify(raw, ad, cd, sig)
    assert parsed["user_present"] and parsed["sign_count"] == 0
    with pytest.raises(_a.AuthError):
        _verify(raw, ad, cd, b"\x00" * 64)


def test_webauthn_rejects_replay_wrong_origin_rp_and_ceremony():
    priv = 0x1234
    raw, ad, cd, sig = _assertion(priv)
    with pytest.raises(_a.AuthError, match="challenge"):
        _verify(raw, ad, cd, sig, expected_challenge=b"a-different-challenge")
    with pytest.raises(_a.AuthError, match="origin"):
        _verify(raw, ad, cd, sig, expected_origin="https://evil.com")
    with pytest.raises(_a.AuthError, match="rpId"):
        _verify(raw, ad, cd, sig, rp_id="evil.com")
    raw2, ad2, cd2, sig2 = _assertion(priv, ctype="webauthn.create")
    with pytest.raises(_a.AuthError, match="ceremony"):
        _verify(raw2, ad2, cd2, sig2)


def test_webauthn_sign_count_must_increase():
    raw, ad, cd, sig = _assertion(0x4242, count=5)
    assert _verify(raw, ad, cd, sig, prior_sign_count=4)["sign_count"] == 5
    with pytest.raises(_a.AuthError, match="sign count"):
        _verify(raw, ad, cd, sig, prior_sign_count=5)


def test_webauthn_accepts_der_signatures_from_real_crypto():
    ec = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ec")
    from cryptography.hazmat.primitives import hashes, serialization
    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.public_key().public_bytes(serialization.Encoding.X962,
                                        serialization.PublicFormat.UncompressedPoint)
    _r, ad, cd, _s = _assertion(1)
    der = key.sign(ad + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
    assert der[0] == 0x30  # what browsers send
    assert _verify(raw, ad, cd, der)["user_present"]
    x = int.from_bytes(raw[1:33], "big")
    y = int.from_bytes(raw[33:], "big")
    r, s = _a._der_to_rs(der)
    msg = ad + hashlib.sha256(cd).digest()
    assert _a._ecdsa_p256_verify_pure(x, y, msg, r, s)            # fallback agrees
    assert not _a._ecdsa_p256_verify_pure(x, y, msg + b"x", r, s)


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
