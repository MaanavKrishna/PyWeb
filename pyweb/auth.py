"""Auth: password hashing, signed sessions, RBAC/permissions, CSRF,
OAuth, magic links, TOTP."""

from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
import urllib.request

_HASH_ALGO = "pbkdf2_sha256"
_ITERATIONS = 260_000


class AuthError(Exception):
    """Base class for auth failures."""


class NotAuthenticated(AuthError):
    """Raised when a request has no usable session."""


class Forbidden(AuthError):
    """Raised when a session lacks the required role/permission."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def hash_password(password, *, salt=None, rounds=None, iterations=_ITERATIONS):
    if salt is None:
        salt_bytes = secrets.token_bytes(16)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt_bytes, iterations)
        return f"{_HASH_ALGO}${iterations}${_b64e(salt_bytes)}${_b64e(dk)}"
    salt_bytes = salt if isinstance(salt, bytes) else bytes.fromhex(salt)
    n = rounds if rounds is not None else iterations
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt_bytes, n)
    return f"pbkdf2${n}${salt_bytes.hex()}${dk.hex()}"


def verify_password(password, stored):
    try:
        parts = stored.split("$")
        if len(parts) != 4:
            return False
        algo, iters, salt_part, dk_part = parts
        iters_i = int(iters)
        if algo == _HASH_ALGO:
            salt = _b64d(salt_part)
            expected = _b64d(dk_part)
        else:
            salt = bytes.fromhex(salt_part)
            expected = bytes.fromhex(dk_part)
    except (ValueError, base64.binascii.Error):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iters_i)
    if algo == _HASH_ALGO:
        return hmac.compare_digest(candidate, expected)
    return hmac.compare_digest(candidate.hex(), expected.hex() if isinstance(expected, bytes) else dk_part)


class SessionStore:
    def __init__(self, secret, *, ttl=86400):
        self.secret = secret.encode() if isinstance(secret, str) else secret
        self.ttl = ttl
        self._data: dict[str, dict] = {}

    def _sign(self, sid):
        return hmac.new(self.secret, sid.encode(), hashlib.sha256).hexdigest()

    def create(self, user_id, extra=None):
        sid = secrets.token_urlsafe(24)
        self._data[sid] = {"user_id": user_id, "created": time.time(), **(extra or {})}
        return f"{sid}.{self._sign(sid)}"

    def get(self, token):
        try:
            sid, sig = token.split(".")
        except (ValueError, AttributeError):
            return None
        if not hmac.compare_digest(sig, self._sign(sid)):
            return None
        sess = self._data.get(sid)
        if not sess or time.time() - sess["created"] > self.ttl:
            self._data.pop(sid, None)
            return None
        return sess

    def destroy(self, token):
        sid = (token or "").split(".")[0]
        self._data.pop(sid, None)


def _session_of(args, kwargs):
    if "session" in kwargs:
        return kwargs["session"]
    return args[0] if args and isinstance(args[0], dict) else None


def required(fn):
    fn.__pyweb_auth__ = "required"

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        session = _session_of(args, kwargs)
        if session is None and not getattr(fn, "__pyweb_auth_enforce__", True):
            return fn(*args, **kwargs)
        if not isinstance(session, dict) or "sub" not in session:
            if "session" in kwargs or args:
                raise NotAuthenticated("login required")
            return fn(*args, **kwargs)
        return fn(*args, **kwargs)

    wrapper.__pyweb_auth__ = "required"
    return wrapper


def permission(name):
    def deco(fn):
        perms = getattr(fn, "__pyweb_permissions__", [])
        fn.__pyweb_permissions__ = [*perms, name]

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            session = _session_of(args, kwargs)
            if session is not None:
                if not isinstance(session, dict) or "sub" not in session:
                    raise NotAuthenticated("login required")
                if name not in session.get("roles", []):
                    raise Forbidden(f"requires role {name!r}")
            return fn(*args, **kwargs)

        wrapper.__pyweb_permissions__ = fn.__pyweb_permissions__
        return wrapper
    return deco


def can(user_roles, fn):
    need = getattr(fn, "__pyweb_permissions__", [])
    return all(p in (user_roles or []) for p in need)


SESSION_COOKIE = "pyweb_session"
LOGIN_URL = "/login"


def session_from_request(req, secret: str, max_age: int = 3600):
    """Extract + verify the session from a ``Request``'s cookies/headers.

    Accepts the ``pyweb_session`` cookie or ``Authorization: Bearer``.
    Returns the payload dict, or ``None`` when absent/invalid/expired.
    """
    raw = ""
    if getattr(req, "cookies", None) and req.cookies.get(SESSION_COOKIE):
        raw = req.cookies[SESSION_COOKIE]
    else:
        auth = (getattr(req, "headers", None) or {}).get("Authorization", "")
        if auth.startswith("Bearer "):
            raw = auth[len("Bearer "):]
    if not raw:
        return None
    return verify_session(raw, secret, max_age)


def require_session(req, secret: str, max_age: int = 3600):
    """Return ``(session, None)`` when authenticated, else ``(None, redirect)``.

    The redirect is a ``302`` to ``/login?next=<path>`` per the QA-006
    contract: unauthenticated page access redirects; API/RPC callers
    distinguish the ``Location: /login`` response instead of a 200.
    """
    from pyweb.runtime.server import Response
    session = session_from_request(req, secret, max_age)
    if session is not None and "sub" in session:
        return session, None
    return None, Response(302, "", {"Location": f"{LOGIN_URL}?next={req.path}"})


def login_response(user_id, secret: str, *, extra=None, next_url="/",
                   max_age: int = 3600, secure: bool = False):
    """``302`` to ``next_url`` with a ``Set-Cookie: pyweb_session=...``.

    The cookie carries ``Max-Age`` matching the server-side session TTL so
    browsers discard stale tokens instead of hoarding them. ``secure=True``
    adds ``Secure`` — always set it in production behind HTTPS; it stays
    off by default only so localhost dev over plain HTTP keeps working.
    """
    from pyweb.runtime.server import Response
    token = issue_session({"sub": user_id, **(extra or {})}, secret, max_age)
    flags = (f"HttpOnly; Path=/; Max-Age={int(max_age)}; SameSite=Lax"
             + ("; Secure" if secure else ""))
    return Response(302, "", {
        "Location": next_url,
        "Set-Cookie": f"{SESSION_COOKIE}={token}; {flags}",
    })


def warn_if_insecure_cookies(secure: bool, *, host: str = "") -> str | None:
    """Return a warning when session cookies would go out without ``Secure``
    on a non-local host. ``pyweb serve`` logs it at startup."""
    if secure:
        return None
    if (host or "").split(":")[0] in ("127.0.0.1", "localhost", "::1", ""):
        return None
    return ("auth cookies lack the Secure flag on a non-local host "
            f"({host!r}): pass secure=True (login_response) and serve "
            "behind HTTPS, or sessions are exposed to network sniffing")


def logout_response(next_url="/", *, secure: bool = False):
    """Clear the session cookie and redirect to ``next_url``."""
    from pyweb.runtime.server import Response
    flags = ("HttpOnly; Path=/; Max-Age=0; SameSite=Lax"
             + ("; Secure" if secure else ""))
    return Response(302, "", {
        "Location": next_url,
        "Set-Cookie": f"{SESSION_COOKIE}=; {flags}",
    })


def _sign(payload_b64: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()


def issue_session(data: dict, secret: str, max_age: int = 3600) -> str:
    """Issue a ``base64url(payload).hexhmac`` stateless session cookie value."""
    payload = dict(data)
    payload.setdefault("iat", int(time.time()))
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    return f"{payload_b64}.{_sign(payload_b64, secret)}"


def verify_session(cookie: str, secret: str, max_age: int = 3600):
    """Verify a stateless session cookie; return payload dict or ``None``."""
    try:
        payload_b64, sig = cookie.rsplit(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(payload_b64, secret), sig):
        return None
    try:
        payload = json.loads(_b64d(payload_b64))
    except (ValueError, base64.binascii.Error, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    iat = payload.get("iat")
    if not isinstance(iat, int) or time.time() - iat > max_age:
        return None
    return payload


_PROVIDERS = {
    "google": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
    },
    "github": {
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
    },
}


class OAuthClient:
    """OAuth authorization-code client using only stdlib urllib."""

    def __init__(self, provider, client_id, client_secret, redirect_uri, scope=None):
        if provider not in _PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}")
        self.provider = provider
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scope = scope or (["openid", "email", "profile"] if provider == "google" else ["read:user"])

    def authorize_url(self, state):
        params = urllib.parse.urlencode({
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "code": "code",
            "scope": " ".join(self.scope),
            "state": state,
        })
        return f"{_PROVIDERS[self.provider]['authorize']}?{params}"

    def exchange_code(self, code, timeout=10):
        data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        }).encode()
        req = urllib.request.Request(_PROVIDERS[self.provider]["token"], data=data,
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())


def issue_magic_token(secret: str, email: str, ttl: int = 900) -> str:
    payload = {"email": email, "exp": int(time.time()) + ttl}
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    return f"{payload_b64}.{_sign(payload_b64, secret)}"


def verify_magic_token(secret: str, token: str, max_age: int = 900):
    try:
        payload_b64, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(payload_b64, secret), sig):
        return None
    try:
        payload = json.loads(_b64d(payload_b64))
    except (ValueError, base64.binascii.Error, json.JSONDecodeError):
        return None
    if time.time() > payload.get("exp", 0):
        return None
    return payload.get("email")


_TOTP_STEP = 30
_TOTP_DIGITS = 6


def _totp_counter(for_time: int) -> int:
    return int(for_time) // _TOTP_STEP


def totp(secret: bytes, for_time: int | None = None, digits=_TOTP_DIGITS) -> str:
    import struct
    counter = _totp_counter(int(time.time()) if for_time is None else for_time)
    mac = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def verify_totp(secret: bytes, code: str, for_time: int | None = None, window: int = 1) -> bool:
    now = int(time.time()) if for_time is None else for_time
    for skew in range(-window, window + 1):
        if hmac.compare_digest(totp(secret, now + skew * _TOTP_STEP), str(code)):
            return True
    return False


def csrf_token(secret, session_id):
    return hmac.new(secret.encode() if isinstance(secret, str) else secret,
                    session_id.encode(), hashlib.sha256).hexdigest()


def verify_csrf(secret, session_id, token):
    return hmac.compare_digest(csrf_token(secret, session_id), token or "")


# ---------------------------------------------------------------------------
# Policy-based authorization (RBAC v1)
#
# Why policies, not just role lists: ``@permission("admin")`` checks a
# single role, but real apps need rules like "authors can edit their own
# posts". :class:`Policy` lets teams declare named rules once and reuse
# them from both server functions and RPC gates.
# ---------------------------------------------------------------------------

class Policy:
    """Named authorization rule set.

    ``Policy("post:edit").allow("admin").allow("author", owner_field="author_id")``
    """

    def __init__(self, name):
        self.name = name
        self._rules: list[tuple] = []

    def allow(self, role, *, owner_field=None):
        self._rules.append((role, owner_field))
        return self

    def check(self, session, resource=None):
        if not isinstance(session, dict) or "sub" not in session:
            raise NotAuthenticated("login required")
        roles = set(session.get("roles", []))
        for role, owner_field in self._rules:
            if role not in roles:
                continue
            if owner_field is None:
                return True
            if resource is not None:
                owner = (resource.get(owner_field)
                         if isinstance(resource, dict)
                         else getattr(resource, owner_field, None))
                if owner == session.get("sub"):
                    return True
        raise Forbidden(f"policy {self.name!r} denied")

    def __call__(self, fn):
        policy = self

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            session = _session_of(args, kwargs)
            resource = kwargs.get("resource", args[1] if len(args) > 1 else None)
            policy.check(session, resource)
            return fn(*args, **kwargs)

        wrapper.__pyweb_permissions__ = [*getattr(fn, "__pyweb_permissions__", []),
                                         f"policy:{self.name}"]
        return wrapper


def rotate_session(store: SessionStore, old_token, *, extra=None):
    """Sliding expiration: destroy the old token, issue a fresh one.

    Call on each authenticated request (or every N minutes) to bound the
    lifetime of a stolen cookie. Preserves user_id + extra claims.
    """
    sess = store.get(old_token)
    if sess is None:
        return None
    store.destroy(old_token)
    user_id = sess.pop("user_id", None)
    merged = {**sess, **(extra or {})}
    return store.create(user_id, extra=merged)


# ---------------------------------------------------------------------------
# WebAuthn (passkeys) — verification side, stdlib-only.
#
# Scope: verify assertion responses (login). Registration (attestation)
# verification is intentionally out of scope for v1 — teams enroll via
# the platform authenticator and import the credential public key.
# ES256 (COSE -7) over SHA-256, matching what iCloud/Google password
# managers and platform authenticators emit.
# ---------------------------------------------------------------------------

def _cbor_first(data: bytes):
    """Minimal CBOR head decode: returns (value, rest) for int/bytes/text.

    Only what WebAuthn authData parsing needs — not a general decoder.
    """
    if not data:
        raise ValueError("empty cbor")
    ib, rest = data[0], data[1:]
    major, info = ib >> 5, ib & 0x1F
    if info < 24:
        num, tail = info, rest
    elif info == 24:
        num, tail = rest[0], rest[1:]
    elif info == 25:
        num = int.from_bytes(rest[:2], "big")
        tail = rest[2:]
    elif info == 26:
        num = int.from_bytes(rest[:4], "big")
        tail = rest[4:]
    else:
        raise ValueError(f"unsupported cbor info {info}")
    if major == 0:
        return num, tail
    if major == 1:
        return -1 - num, tail
    if major == 2:
        return tail[:num], tail[num:]
    if major == 3:
        return tail[:num].decode("utf-8"), tail[num:]
    raise ValueError(f"unsupported cbor major {major}")


def parse_auth_data(auth_data: bytes) -> dict:
    """Parse WebAuthn ``authenticatorData`` into its fields."""
    if len(auth_data) < 37:
        raise ValueError("authenticatorData too short")
    return {
        "rp_id_hash": auth_data[:32],
        "flags": auth_data[32],
        "sign_count": int.from_bytes(auth_data[33:37], "big"),
        "attested": auth_data[37:],
        "user_present": bool(auth_data[32] & 0x01),
        "user_verified": bool(auth_data[32] & 0x04),
    }


def _b64url_decode(data) -> bytes:
    if isinstance(data, bytes):
        return data
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _der_to_rs(sig: bytes):
    """Parse an ASN.1 DER ``ECDSA-Sig-Value`` into ``(r, s)``."""
    def read_len(buf, i):
        n = buf[i]
        i += 1
        if n < 0x80:
            return n, i
        k = n & 0x7F
        if k == 0 or k > 2:
            raise ValueError("bad DER length")
        return int.from_bytes(buf[i:i + k], "big"), i + k

    if len(sig) < 8 or sig[0] != 0x30:
        raise ValueError("not a DER sequence")
    total, i = read_len(sig, 1)
    if i + total != len(sig):
        raise ValueError("DER length mismatch")
    out = []
    for _ in range(2):
        if sig[i] != 0x02:
            raise ValueError("expected DER integer")
        n, i = read_len(sig, i + 1)
        out.append(int.from_bytes(sig[i:i + n], "big"))
        i += n
    if i != len(sig):
        raise ValueError("trailing DER bytes")
    return out[0], out[1]


def verify_webauthn_assertion(*, credential_public_key: bytes,
                              auth_data: bytes, client_data_json: bytes,
                              signature: bytes, rp_id: str,
                              expected_challenge, expected_origin,
                              require_user_verification=False,
                              prior_sign_count=None) -> dict:
    """Verify a WebAuthn (passkey) login assertion. ES256 / P-256 only.

    * ``credential_public_key``: raw uncompressed P-256 point
      (``0x04 || x || y``) stored at registration
      (:func:`cose_to_raw_point` converts COSE keys).
    * ``signature``: as sent by the browser (ASN.1 DER); raw 64-byte
      ``r || s`` is accepted too.
    * ``expected_challenge``: the challenge you issued for this login
      (bytes, or base64url text) — prevents replaying old assertions.
    * ``expected_origin``: e.g. ``"https://example.com"`` (or a list).
    * ``prior_sign_count``: the stored counter; a non-increasing counter
      signals a cloned authenticator and is rejected.

    Returns the parsed authenticator data; raises :class:`AuthError`.
    The signature is checked with the ``cryptography`` package when it is
    installed (``pip install "pyweb-stack[crypto]"``) and with a pure-Python
    fallback otherwise.
    """
    parsed = parse_auth_data(auth_data)
    if not hmac.compare_digest(parsed["rp_id_hash"], hashlib.sha256(rp_id.encode()).digest()):
        raise AuthError("rpId hash mismatch")
    if not parsed["user_present"]:
        raise AuthError("user presence flag not set")
    if require_user_verification and not parsed["user_verified"]:
        raise AuthError("user verification required")
    if len(credential_public_key) != 65 or credential_public_key[0] != 0x04:
        raise AuthError("unsupported credential key (need raw P-256)")
    try:
        client = json.loads(client_data_json)
    except ValueError as exc:
        raise AuthError("invalid clientDataJSON") from exc
    if not isinstance(client, dict) or client.get("type") != "webauthn.get":
        raise AuthError("wrong ceremony type")
    try:
        got_challenge = _b64url_decode(client.get("challenge") or "")
    except (ValueError, TypeError) as exc:
        raise AuthError("malformed challenge") from exc
    if not expected_challenge or not hmac.compare_digest(got_challenge, _b64url_decode(expected_challenge)):
        raise AuthError("challenge mismatch")
    origins = [expected_origin] if isinstance(expected_origin, str) else list(expected_origin or [])
    if client.get("origin") not in origins:
        raise AuthError("origin mismatch")
    if prior_sign_count and parsed["sign_count"] and parsed["sign_count"] <= prior_sign_count:
        raise AuthError("sign count did not increase (possible cloned authenticator)")
    msg = auth_data + hashlib.sha256(client_data_json).digest()
    try:
        if len(signature) == 64:
            r, s_ = int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
        else:
            r, s_ = _der_to_rs(signature)
    except (ValueError, IndexError) as exc:
        raise AuthError("malformed signature") from exc
    x = int.from_bytes(credential_public_key[1:33], "big")
    y = int.from_bytes(credential_public_key[33:65], "big")
    if not _ecdsa_p256_verify_rs(x, y, msg, r, s_):
        raise AuthError("invalid assertion signature")
    return parsed


def cose_to_raw_point(cose_key: bytes) -> bytes:
    """Convert a COSE_Key (ES256, kty EC2) to a raw 65-byte P-256 point."""
    if not cose_key or cose_key[0] != 0xA5:
        raise ValueError("expected COSE map(5)")
    rest = cose_key[1:]
    vals = {}
    for _ in range(5):
        k, rest = _cbor_first(rest)
        v, rest = _cbor_first(rest)
        vals[k] = v
    if vals.get(1) != 2 or vals.get(3) != -7:
        raise ValueError("only COSE EC2/ES256 supported")
    x, y = vals.get(-2), vals.get(-3)
    if not (isinstance(x, bytes) and isinstance(y, bytes)
            and len(x) == 32 and len(y) == 32):
        raise ValueError("bad COSE x/y coordinates")
    return b"\x04" + x + y


_P256 = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_P256_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
_P256_GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
_P256_GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5
_P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def _p256_point_add(p, q):
    # Curve: y^2 = x^3 - 3x + b (NIST P-256, a = -3).
    if p is None:
        return q
    if q is None:
        return p
    x1, y1 = p
    x2, y2 = q
    if x1 == x2 and (y1 + y2) % _P256 == 0:
        return None
    if p == q:
        lam = (3 * x1 * x1 - 3) * pow(2 * y1, -1, _P256) % _P256
    else:
        lam = ((y2 - y1) * pow((x2 - x1) % _P256, -1, _P256)) % _P256
    x3 = (lam * lam - x1 - x2) % _P256
    return (x3, (lam * (x1 - x3) - y1) % _P256)


def _p256_on_curve(x, y) -> bool:
    return (y * y - (x * x * x - 3 * x + _P256_B)) % _P256 == 0


def _p256_scalar_mult(k, point):
    res = None
    add = point
    while k:
        if k & 1:
            res = _p256_point_add(res, add)
        add = _p256_point_add(add, add)
        k >>= 1
    return res


def _ecdsa_p256_verify(x, y, msg, signature: bytes) -> bool:
    """Verify a raw 64-byte ``r || s`` ECDSA P-256/SHA-256 signature."""
    if len(signature) != 64:
        return False
    return _ecdsa_p256_verify_rs(x, y, msg, int.from_bytes(signature[:32], "big"),
                                 int.from_bytes(signature[32:], "big"))


def _ecdsa_p256_verify_rs(x, y, msg, r, s) -> bool:
    if not (0 < x < _P256 and 0 < y < _P256) or not _p256_on_curve(x, y):
        return False
    if not (0 < r < _P256_N and 0 < s < _P256_N):
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    except ImportError:
        return _ecdsa_p256_verify_pure(x, y, msg, r, s)
    key = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    try:
        key.verify(encode_dss_signature(r, s), msg, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


def _ecdsa_p256_verify_pure(x, y, msg, r, s) -> bool:
    """Pure-Python fallback. Verification uses only public data, so timing
    side channels do not leak secrets; it is slow (~50/s), fine for logins."""
    e = int.from_bytes(hashlib.sha256(msg).digest(), "big")
    w = pow(s, -1, _P256_N)
    u1 = (e * w) % _P256_N
    u2 = (r * w) % _P256_N
    pt = _p256_point_add(_p256_scalar_mult(u1, (_P256_GX, _P256_GY)),
                         _p256_scalar_mult(u2, (x, y)))
    if pt is None:
        return False
    return pt[0] % _P256_N == r


def oidc_userinfo(endpoint: str, access_token: str, timeout=10) -> dict:
    """Fetch OIDC ``userinfo`` claims with a bearer token (stdlib)."""
    req = urllib.request.Request(endpoint, headers={
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())
