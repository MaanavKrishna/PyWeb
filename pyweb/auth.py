"""Auth: password hashing, signed sessions, RBAC/permissions, CSRF."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time


def hash_password(password, *, salt=None, rounds=100_000):
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2${rounds}${salt.hex()}${dk.hex()}"


def verify_password(password, stored):
    try:
        _alg, rounds, salt_hex, dk_hex = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
    return hmac.compare_digest(dk.hex(), dk_hex)


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


def required(fn):
    fn.__pyweb_auth__ = "required"
    return fn


def permission(name):
    def deco(fn):
        perms = getattr(fn, "__pyweb_permissions__", [])
        fn.__pyweb_permissions__ = [*perms, name]
        return fn
    return deco


def can(user_roles, fn):
    need = getattr(fn, "__pyweb_permissions__", [])
    return all(p in (user_roles or []) for p in need)


def csrf_token(secret, session_id):
    return hmac.new(secret.encode() if isinstance(secret, str) else secret,
                    session_id.encode(), hashlib.sha256).hexdigest()


def verify_csrf(secret, session_id, token):
    return hmac.compare_digest(csrf_token(secret, session_id), token or "")
