"""Signing keys: one secret in, a separate key per purpose out, with rotation.

``PYWEB_AUTH_SECRET`` is the root. Sessions, live-update feeds and live
query specs are each signed with their own key derived from it
(HMAC-SHA256 of the root and the purpose), so a token made for one
purpose can never pass as another.

Rotating the secret: set the new one as ``PYWEB_AUTH_SECRET`` and move the
old one to ``PYWEB_AUTH_SECRET_PREVIOUS`` (comma-separate several). New
tokens use the new key; tokens signed with a previous one keep working
until they expire, so nobody is logged out.
"""

from __future__ import annotations

import hashlib
import hmac

PURPOSES = ("session", "feed", "live")


def derive(root, purpose: str) -> str:
    """The key for ``purpose`` derived from secret ``root``."""
    key = root.encode() if isinstance(root, str) else root
    return hmac.new(key, b"pyweb/" + purpose.encode(), hashlib.sha256).hexdigest()


def previous_secrets() -> list[str]:
    """Secrets from ``PYWEB_AUTH_SECRET_PREVIOUS`` (still accepted, never used to sign)."""
    from .config import settings
    return list(settings().previous_secrets)


def signing_key(root, purpose: str) -> str:
    return derive(root, purpose)


def verify_keys(root, purpose: str) -> list[str]:
    """Keys a token for ``purpose`` may be signed with, newest first (the current
    secret, then each of ``PYWEB_AUTH_SECRET_PREVIOUS``), each derived for ``purpose``.

    A token signed for one purpose never verifies for another, and the raw secret
    itself is never a key (0.4.4 accepted it for tokens from older versions).
    """
    roots = [root, *previous_secrets()]
    out = [derive(r, purpose) for r in roots]
    seen, unique = set(), []
    for k in out:
        if k not in seen:
            seen.add(k)
            unique.append(k)
    return unique


def any_valid(candidates, check) -> bool:
    """True when ``check(key)`` passes for any key in ``candidates`` (each checked in constant time)."""
    ok = False
    for key in candidates:
        ok = check(key) or ok   # no early exit: timing doesn't reveal which key matched
    return ok
