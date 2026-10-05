"""Passkeys (WebAuthn): registering a credential and signing in with it.

Pure Python. Supports the two algorithms authenticators use in practice:
ES256 (COSE -7, P-256) and RS256 (COSE -257, e.g. Windows Hello).
Attestation statements are not checked (``attestation: "none"``), which is
the right choice for sign-in: we need the credential's public key, not a
proof of which device model made it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from pyweb.auth import AuthError, _der_to_rs, _ecdsa_p256_verify_rs, parse_auth_data

ES256 = -7
RS256 = -257
ALGORITHMS = (ES256, RS256)


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def unb64url(text) -> bytes:
    if isinstance(text, (bytes, bytearray)):
        return bytes(text)
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ------------------------------------------------------------------- CBOR

class _Reader:
    def __init__(self, data):
        self.data = data
        self.i = 0

    def take(self, n):
        if self.i + n > len(self.data):
            raise ValueError("CBOR data ends early")
        out = self.data[self.i:self.i + n]
        self.i += n
        return out

    def item(self, depth=0):
        if depth > 16:
            raise ValueError("CBOR nested too deeply")
        ib = self.take(1)[0]
        major, info = ib >> 5, ib & 0x1F
        if info < 24:
            n = info
        elif info in (24, 25, 26, 27):
            n = int.from_bytes(self.take(1 << (info - 24)), "big")
        else:
            raise ValueError("indefinite-length CBOR isn't supported")
        if major == 0:
            return n
        if major == 1:
            return -1 - n
        if major == 2:
            return bytes(self.take(n))
        if major == 3:
            return self.take(n).decode("utf-8")
        if major == 4:
            if n > 1000:
                raise ValueError("CBOR array too long")
            return [self.item(depth + 1) for _ in range(n)]
        if major == 5:
            if n > 1000:
                raise ValueError("CBOR map too long")
            out = {}
            for _ in range(n):
                key = self.item(depth + 1)
                if isinstance(key, (list, dict)):
                    raise ValueError("CBOR map keys must be simple values")
                out[key] = self.item(depth + 1)
            return out
        if major == 7:
            return {20: False, 21: True, 22: None}.get(info)
        raise ValueError(f"unsupported CBOR type {major}")


def cbor_loads(data: bytes):
    """Decode one CBOR item; ``(value, bytes_used)``."""
    r = _Reader(bytes(data))
    value = r.item()
    return value, r.i


# ------------------------------------------------------------- client data

def _check_client(client_data_json, kind, challenge, origins):
    try:
        client = json.loads(client_data_json)
    except ValueError as exc:
        raise AuthError("invalid clientDataJSON") from exc
    if not isinstance(client, dict) or client.get("type") != kind:
        raise AuthError("wrong ceremony type")
    try:
        got = unb64url(client.get("challenge") or "")
    except (ValueError, TypeError) as exc:
        raise AuthError("malformed challenge") from exc
    if not challenge or not hmac.compare_digest(got, unb64url(challenge)):
        raise AuthError("challenge mismatch")
    if client.get("origin") not in origins:
        raise AuthError("origin mismatch")
    return client


def _check_rp(parsed, rp_id, require_uv):
    if not hmac.compare_digest(parsed["rp_id_hash"], hashlib.sha256(rp_id.encode()).digest()):
        raise AuthError("rpId hash mismatch")
    if not parsed["user_present"]:
        raise AuthError("user presence flag not set")
    if require_uv and not parsed["user_verified"]:
        raise AuthError("user verification required")


# ------------------------------------------------------------- registration

def verify_registration(*, attestation_object, client_data_json, rp_id, challenge, origins, require_uv=False):
    """Check a ``navigator.credentials.create()`` result.

    Returns ``{"credential_id": str, "public_key": bytes (COSE), "algorithm": int, "sign_count": int}``.
    """
    att, _ = cbor_loads(unb64url(attestation_object))
    if not isinstance(att, dict) or "authData" not in att:
        raise AuthError("invalid attestation object")
    _check_client(unb64url(client_data_json), "webauthn.create", challenge, origins)
    auth_data = att["authData"]
    parsed = parse_auth_data(auth_data)
    _check_rp(parsed, rp_id, require_uv)
    if not parsed["flags"] & 0x40:
        raise AuthError("no credential in the response")
    rest = parsed["attested"]
    if len(rest) < 18:
        raise AuthError("attested credential data too short")
    length = int.from_bytes(rest[16:18], "big")
    cred_id = rest[18:18 + length]
    if len(cred_id) != length or not 16 <= length <= 1023:
        raise AuthError("bad credential id")
    cose_bytes = rest[18 + length:]
    key, used = cbor_loads(cose_bytes)
    cose_bytes = cose_bytes[:used]
    alg = key.get(3) if isinstance(key, dict) else None
    if alg not in ALGORITHMS:
        raise AuthError("unsupported passkey algorithm (ES256 or RS256 only)")
    public_key_check(key)
    return {"credential_id": b64url(cred_id), "public_key": cose_bytes, "algorithm": alg,
            "sign_count": parsed["sign_count"]}


def public_key_check(key):
    if key.get(3) == ES256:
        if key.get(1) != 2 or key.get(-1) != 1 or len(key.get(-2, b"")) != 32 or len(key.get(-3, b"")) != 32:
            raise AuthError("bad P-256 key")
    elif key.get(3) == RS256:
        if key.get(1) != 3 or not key.get(-1) or not key.get(-2):
            raise AuthError("bad RSA key")
        if len(key[-1]) < 256:
            raise AuthError("RSA key too short (need 2048 bits)")


# --------------------------------------------------------------- sign-in

def _rsa_pkcs1_sha256_verify(n_bytes, e_bytes, msg, sig):
    n = int.from_bytes(n_bytes, "big")
    e = int.from_bytes(e_bytes, "big")
    k = len(n_bytes)
    if len(sig) != k:
        return False
    m = pow(int.from_bytes(sig, "big"), e, n).to_bytes(k, "big")
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(msg).digest()
    expected = b"\x00\x01" + b"\xff" * (k - len(digest_info) - 3) + b"\x00" + digest_info
    return hmac.compare_digest(m, expected)


def verify_assertion(*, public_key, authenticator_data, client_data_json, signature, rp_id, challenge,
                     origins, prior_sign_count=0, require_uv=False):
    """Check a ``navigator.credentials.get()`` result against a stored COSE key.

    Returns the new signature counter.
    """
    auth_data = unb64url(authenticator_data)
    client_raw = unb64url(client_data_json)
    sig = unb64url(signature)
    parsed = parse_auth_data(auth_data)
    _check_rp(parsed, rp_id, require_uv)
    _check_client(client_raw, "webauthn.get", challenge, origins)
    if prior_sign_count and parsed["sign_count"] and parsed["sign_count"] <= prior_sign_count:
        raise AuthError("sign count did not increase (possible cloned authenticator)")
    key, _ = cbor_loads(public_key)
    msg = auth_data + hashlib.sha256(client_raw).digest()
    alg = key.get(3)
    if alg == ES256:
        try:
            r, s = _der_to_rs(sig) if len(sig) != 64 else (int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
        except (ValueError, IndexError) as exc:
            raise AuthError("malformed signature") from exc
        ok = _ecdsa_p256_verify_rs(int.from_bytes(key[-2], "big"), int.from_bytes(key[-3], "big"), msg, r, s)
    elif alg == RS256:
        ok = _rsa_pkcs1_sha256_verify(key[-1], key[-2], msg, sig)
    else:
        raise AuthError("unsupported passkey algorithm")
    if not ok:
        raise AuthError("invalid passkey signature")
    return parsed["sign_count"]
