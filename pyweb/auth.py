"""PyWeb authentication: passwords, sessions, RBAC, OAuth, magic links, TOTP.

Session cookie format (signed cookies):
    base64url(payload).hexhmac

* ``payload`` is the JSON session dict (must include ``iat`` issued-at epoch
  seconds) encoded with URL-safe base64 **without** ``=`` padding.
* ``hexhmac`` is ``hmac.new(secret, payload_b64, sha256).hexdigest()``.
* Verification recomputes the hex digest with a timing-safe compare and
  rejects cookies whose ``iat`` is older than ``max_age`` seconds.

Only the standard library is used (hashlib / hmac / secrets / urllib).
"""
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
_TOTP_STEP = 30
_TOTP_DIGITS = 6


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


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256 and a random 16-byte salt."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                             salt, _ITERATIONS)
    return f"{_HASH_ALGO}${_ITERATIONS}${_b64e(salt)}${_b64e(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored hash using a timing-safe compare."""
    try:
        algo, iters, salt_b64, dk_b64 = stored.split("$")
    except ValueError:
        return False
    if algo != _HASH_ALGO:
        return False
    try:
        iters_i = int(iters)
        salt = _b64d(salt_b64)
        expected = _b64d(dk_b64)
    except (ValueError, base64.binascii.Error):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                    salt, iters_i)
    return hmac.compare_digest(candidate, expected)


def _sign(payload_b64: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload_b64.encode(),
                    hashlib.sha256).hexdigest()


def issue_session(data: dict, secret: str, max_age: int = 3600) -> str:
    """Issue a ``base64url(payload).hexhmac`` session cookie value."""
    payload = dict(data)
    payload.setdefault("iat", int(time.time()))
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    return f"{payload_b64}.{_sign(payload_b64, secret)}"


def verify_session(cookie: str, secret: str,
                   max_age: int = 3600) -> dict | None:
    """Verify a session cookie; return payload dict or ``None``."""
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


def _session_of(args, kwargs) -> dict | None:
    if "session" in kwargs:
        return kwargs["session"]
    return args[0] if args and isinstance(args[0], dict) else None


def required(func):
    """Page guard: allow only requests carrying a session dict."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        session = _session_of(args, kwargs)
        if not isinstance(session, dict) or "sub" not in session:
            raise NotAuthenticated("login required")
        return func(*args, **kwargs)

    return wrapper


def permission(role: str):
    """RBAC decorator: require ``role`` in ``session["roles"]``."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            session = _session_of(args, kwargs)
            if not isinstance(session, dict) or "sub" not in session:
                raise NotAuthenticated("login required")
            if role not in session.get("roles", []):
                raise Forbidden(f"requires role {role!r}")
            return func(*args, **kwargs)

        return wrapper

    return decorator


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
    """Minimal authorization-code flow client (urllib only, no deps)."""

    def __init__(self, provider: str, client_id: str,
                 client_secret: str, redirect_uri: str,
                 scope: str = "openid email profile"):
        if provider not in _PROVIDERS:
            raise AuthError(f"unknown provider {provider!r}")
        self.provider = provider
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scope = scope

    def authorize_url(self, state: str) -> str:
        """Build the authorization-code redirect URL."""
        base = _PROVIDERS[self.provider]["authorize"]
        qs = urllib.parse.urlencode({
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": self.scope,
            "state": state,
        })
        return f"{base}?{qs}"

    def exchange_code(self, code: str, timeout: int = 10) -> dict:
        """Exchange an authorization code for tokens via POST."""
        token_url = _PROVIDERS[self.provider]["token"]
        data = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode()
        req = urllib.request.Request(token_url, data=data, headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())


def issue_magic_token(secret: str, email: str, ttl: int = 900) -> str:
    """Issue an email magic-link token: ``b64(json).hexhmac`` with expiry."""
    payload = {"email": email, "exp": int(time.time()) + ttl,
               "nonce": secrets.token_urlsafe(16)}
    raw = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    return f"{raw}.{_sign(raw, secret)}"


def verify_magic_token(secret: str, token: str,
                       max_age: int | None = None) -> str | None:
    """Verify a magic-link token; return the email or ``None``."""
    try:
        raw, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(raw, secret), sig):
        return None
    try:
        payload = json.loads(_b64d(raw))
    except (ValueError, base64.binascii.Error, json.JSONDecodeError):
        return None
    exp = payload.get("exp", 0)
    now = int(time.time())
    if exp < now:
        return None
    if max_age is not None and exp - now > max_age:
        return None
    return payload.get("email")


def _totp_counter(for_time: int) -> int:
    return int(for_time) // _TOTP_STEP


def totp(secret: bytes, for_time: int | None = None,
         digits: int = _TOTP_DIGITS) -> str:
    """Generate a TOTP code per RFC 6238 (HMAC-SHA1, 30s step)."""
    if for_time is None:
        for_time = int(time.time())
    msg = _totp_counter(for_time).to_bytes(8, "big")
    digest = hmac.new(secret, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = ((digest[offset] & 0x7F) << 24
            | digest[offset + 1] << 16
            | digest[offset + 2] << 8
            | digest[offset + 3])
    return str(code % (10 ** digits)).zfill(digits)


def verify_totp(secret: bytes, code: str,
                for_time: int | None = None,
                window: int = 1) -> bool:
    """Verify a TOTP code, accepting +/- ``window`` time steps."""
    if for_time is None:
        for_time = int(time.time())
    base = _totp_counter(for_time)
    for delta in range(-window, window + 1):
        candidate = totp(secret, for_time=(base + delta) * _TOTP_STEP)
        if hmac.compare_digest(candidate, code):
            return True
    return False
