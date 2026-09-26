"""Auth: password hashing, signed sessions, RBAC/permissions, CSRF,
OAuth, magic links, TOTP."""

from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
import os
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
                   max_age: int = 3600):
    """``302`` to ``next_url`` with a ``Set-Cookie: pyweb_session=...``."""
    from pyweb.runtime.server import Response
    token = issue_session({"sub": user_id, **(extra or {})}, secret, max_age)
    return Response(302, "", {
        "Location": next_url,
        "Set-Cookie": f"{SESSION_COOKIE}={token}; HttpOnly; Path=/; SameSite=Lax",
    })


def logout_response(next_url="/"):
    """Clear the session cookie and redirect to ``next_url``."""
    from pyweb.runtime.server import Response
    return Response(302, "", {
        "Location": next_url,
        "Set-Cookie": f"{SESSION_COOKIE}=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax",
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
