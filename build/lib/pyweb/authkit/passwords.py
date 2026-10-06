"""Password hashing and checks for the auth kit.

New hashes use **scrypt** (in Python's standard library), or **argon2id**
when ``argon2-cffi`` is installed (``pip install "pyweb-stack[auth]"``).
Older hashes (PBKDF2 from ``pyweb.auth``) still verify, and
:func:`needs_rehash` tells the kit to upgrade them at the next login.

Rules follow NIST SP 800-63B: at least 10 characters, no composition rules,
and passwords known from breaches are refused (checked with the Pwned
Passwords range API: only the first 5 characters of the SHA-1 hash leave
the server). The breach check is on in production; set
``PYWEB_BREACHED_PASSWORDS=0`` to turn it off or ``1`` to turn it on.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import urllib.request

log = logging.getLogger("pyweb.auth")

MIN_LENGTH = 10
MAX_LENGTH = 512
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}


def _b64(raw):
    return base64.b64encode(raw).decode().rstrip("=")


def _unb64(text):
    return base64.b64decode(text + "=" * (-len(text) % 4))


def _argon2():
    try:
        from argon2 import PasswordHasher
        return PasswordHasher()
    except ImportError:
        return None


def hash_password(password):
    """A new hash for ``password`` (argon2id if available, else scrypt)."""
    hasher = _argon2()
    if hasher is not None:
        return hasher.hash(password)
    salt = secrets.token_bytes(16)
    p = SCRYPT
    key = hashlib.scrypt(password.encode(), salt=salt, n=p["n"], r=p["r"], p=p["p"], maxmem=128 * 1024 * 1024,
                         dklen=32)
    return f"scrypt${p['n']}${p['r']}${p['p']}${_b64(salt)}${_b64(key)}"


def verify_password(password, stored):
    """Whether ``password`` matches ``stored`` (any hash this or older versions made)."""
    if not stored or password is None:
        return False
    if stored.startswith("$argon2"):
        hasher = _argon2()
        if hasher is None:
            log.error("an argon2 password hash exists but argon2-cffi isn't installed")
            return False
        try:
            return hasher.verify(stored, password)
        except Exception:  # noqa: BLE001 - argon2 raises its own mismatch errors
            return False
    if stored.startswith("scrypt$"):
        try:
            _, n, r, p, salt, key = stored.split("$")
            got = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                                 maxmem=128 * 1024 * 1024, dklen=len(_unb64(key)))
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(got, _unb64(key))
    from pyweb import auth
    return auth.verify_password(password, stored)


def needs_rehash(stored):
    """Whether a hash should be replaced at the next successful login."""
    if not stored:
        return False
    hasher = _argon2()
    if hasher is not None:
        if not stored.startswith("$argon2"):
            return True
        try:
            return hasher.check_needs_rehash(stored)
        except Exception:  # noqa: BLE001
            return False
    if not stored.startswith("scrypt$"):
        return True
    try:
        _, n, r, p, _salt, _key = stored.split("$")
    except ValueError:
        return True
    return (int(n), int(r), int(p)) != (SCRYPT["n"], SCRYPT["r"], SCRYPT["p"])


_DUMMY = None


def burn_time(password):
    """Spend as long as checking a real password (so unknown emails take the same time)."""
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password(secrets.token_urlsafe(12))
    verify_password(password or "", _DUMMY)


def breach_check_enabled():
    flag = os.environ.get("PYWEB_BREACHED_PASSWORDS")
    if flag is not None:
        return flag.strip().lower() in ("1", "true", "yes", "on")
    return os.environ.get("PYWEB_ENV", "").lower() == "production"


def breached(password, *, timeout=3.0):
    """How many times ``password`` appears in known breaches (0 if not, or if the check can't run)."""
    digest = hashlib.sha1(password.encode(), usedforsecurity=False).hexdigest().upper()  # noqa: S324 - the API's format
    prefix, suffix = digest[:5], digest[5:]
    req = urllib.request.Request(f"https://api.pwnedpasswords.com/range/{prefix}",
                                 headers={"Add-Padding": "true", "User-Agent": "pyweb"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
            body = resp.read().decode()
    except Exception as exc:  # noqa: BLE001 - never block sign-up because the service is down
        log.warning("breached password check unavailable: %s", exc)
        return 0
    for line in body.splitlines():
        found, _, count = line.partition(":")
        if found.strip() == suffix:
            try:
                return int(count)
            except ValueError:
                return 1
    return 0


def problem(password, *, email=None, name=None):
    """Why ``password`` can't be used (a phrase), or None."""
    if not password or len(password) < MIN_LENGTH:
        return f"must be at least {MIN_LENGTH} characters"
    if len(password) > MAX_LENGTH:
        return f"must be at most {MAX_LENGTH} characters"
    low = password.lower()
    for word in (email, (email or "").split("@")[0], name):
        if word and len(word) >= 4 and low == str(word).lower():
            return "can't be your email or name"
    if breach_check_enabled() and breached(password):
        return "appears in a known data breach; choose another"
    return None
