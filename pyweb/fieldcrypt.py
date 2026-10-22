"""Encrypted Model fields: ``phone: str = Field(encrypted=True)``.

Values are encrypted with AES-256-GCM before they reach the database and
decrypted when rows are read, so a leaked backup or a read-only SQL
injection shows only ciphertext. Needs the ``cryptography`` package
(``pip install "pyweb-stack[crypto]"``).

Keys
----
``PYWEB_DATA_KEY`` is the root (any long random string; ``python -c "import
secrets; print(secrets.token_urlsafe(32))"``). Each column gets its own key
derived from it, so a ciphertext copied into another column doesn't decrypt.
It is separate from ``PYWEB_AUTH_SECRET`` on purpose: rotating the session
secret (after a leak, say) must never make stored data unreadable.

In development, without ``PYWEB_DATA_KEY``, a key derived from this machine
and project folder is used, so data written there can only be read there.

Stored form: ``pw1:<key id>:<base64(nonce + ciphertext + tag)>``, with the
table and column as associated data.

Rotation
--------
Set the new key as ``PYWEB_DATA_KEY`` and move the old one to
``PYWEB_DATA_KEY_PREVIOUS`` (comma-separate several). Rows still decrypt
with the old key and are written with the new one when saved; run
``pyweb db reencrypt`` to rewrite every row now, then drop the old key.

Looking rows up
---------------
Ciphertexts are random, so ``where(phone=...)`` can't compare them. With
``Field(encrypted=True, searchable=True)`` PyWeb also stores a *blind
index*: an HMAC of the value (in ``<column>_bidx``), which supports ``==``
and ``in_()`` lookups and nothing else (no ordering, ranges or ``like``).
Equal values have equal HMACs, so someone with the database can tell which
rows share a value, though not what it is.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import os

PREFIX = "pw1"
NONCE = 12


class DecryptError(RuntimeError):
    """A stored value didn't decrypt with any configured key (wrong or missing ``PYWEB_DATA_KEY``)."""


def _require_crypto():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise RuntimeError('encrypted fields need the cryptography package: pip install "pyweb-stack[crypto]"') \
            from None
    return AESGCM


def available():
    try:
        _require_crypto()
    except RuntimeError:
        return False
    return True


def _roots():
    """``(current, [previous...])`` root keys."""
    current = os.environ.get("PYWEB_DATA_KEY", "").strip()
    previous = [k.strip() for k in os.environ.get("PYWEB_DATA_KEY_PREVIOUS", "").split(",") if k.strip()]
    if not current:
        if os.environ.get("PYWEB_ENV", "development") == "production":
            raise RuntimeError("PYWEB_DATA_KEY must be set in production to use encrypted fields")
        from .context import _dev_secret
        current = "dev:" + _dev_secret
    return current, previous


@functools.lru_cache(maxsize=256)
def _keys_for(root, purpose):
    key = hmac.new(root.encode(), b"pyweb-data/" + purpose.encode(), hashlib.sha256).digest()
    kid = hmac.new(root.encode(), b"pyweb-data/kid", hashlib.sha256).hexdigest()[:8]
    return kid, key


def encrypt(table, column, plaintext: bytes) -> str:
    AESGCM = _require_crypto()
    current, _ = _roots()
    kid, key = _keys_for(current, f"field:{table}.{column}")
    nonce = os.urandom(NONCE)
    ct = AESGCM(key).encrypt(nonce, plaintext, f"{table}.{column}".encode())
    return f"{PREFIX}:{kid}:{base64.urlsafe_b64encode(nonce + ct).decode()}"


def is_ciphertext(value):
    return isinstance(value, str) and value.startswith(PREFIX + ":") and value.count(":") >= 2


def decrypt(table, column, stored: str) -> bytes:
    """Plaintext bytes; :class:`DecryptError` if no configured key fits."""
    AESGCM = _require_crypto()
    if not is_ciphertext(stored):
        raise DecryptError(f"{table}.{column} holds a value that isn't encrypted")
    _, kid, body = stored.split(":", 2)
    current, previous = _roots()
    try:
        raw = base64.urlsafe_b64decode(body.encode())
    except ValueError:
        raise DecryptError(f"{table}.{column}: damaged ciphertext") from None
    for root in (current, *previous):
        k, key = _keys_for(root, f"field:{table}.{column}")
        if k != kid:
            continue
        try:
            return AESGCM(key).decrypt(raw[:NONCE], raw[NONCE:], f"{table}.{column}".encode())
        except Exception:  # noqa: BLE001 - cryptography's InvalidTag
            raise DecryptError(f"{table}.{column}: the value was changed or belongs to another column") from None
    raise DecryptError(f"{table}.{column} was encrypted with a key that isn't configured "
                       "(set it in PYWEB_DATA_KEY or PYWEB_DATA_KEY_PREVIOUS)")


def current_kid():
    current, _ = _roots()
    return _keys_for(current, "kid")[0]


def blind_indexes(table, column, plaintext: bytes) -> list[str]:
    """The value's blind index under the current key, then under each previous key."""
    current, previous = _roots()
    out = []
    for root in (current, *previous):
        _, key = _keys_for(root, f"index:{table}.{column}")
        out.append(hmac.new(key, plaintext, hashlib.sha256).hexdigest()[:32])
    return out


def reencrypt(db, models, *, batch=500):
    """Rewrite every encrypted value that isn't under the current ``PYWEB_DATA_KEY`` (and its blind
    index). Returns ``{table: rows rewritten}``. Safe to run while the app serves requests: each
    row is rewritten with the same plaintext, in batches."""
    from .db.dialect import dialect_of
    d = dialect_of(db)
    q = d.quote
    kid = current_kid()
    done = {}
    for model in models:
        meta = model._meta
        fields = [f for f in meta.fields.values() if getattr(f, "encrypted", False)]
        if meta.abstract or not fields:
            continue
        pk = meta.pk
        count, last = 0, None
        while True:
            where = f" WHERE {q(pk.column)} > ?" if last is not None else ""
            rows = db.execute(f"SELECT {q(pk.column)}, {', '.join(q(f.column) for f in fields)} FROM "
                              f"{q(meta.table)}{where} ORDER BY {q(pk.column)} LIMIT {int(batch)}",
                              [last] if last is not None else []).fetchall()
            if not rows:
                break
            for row in rows:
                last = row[0]
                changes = {}
                for f, stored in zip(fields, row[1:]):
                    if stored is None:
                        continue
                    if isinstance(stored, (bytes, bytearray)):
                        stored = stored.decode()
                    if stored.split(":", 2)[1:2] == [kid]:
                        continue
                    value = f.from_db(stored)
                    changes[f.column] = f.to_db(value, d)
                    if f.searchable:
                        changes[f.index_column] = f.blind_indexes(value)[0]
                if changes:
                    db.execute(f"UPDATE {q(meta.table)} SET {', '.join(f'{q(c)} = ?' for c in changes)} "
                               f"WHERE {q(pk.column)} = ?", [*changes.values(), row[0]])
                    count += 1
            if hasattr(db, "commit"):
                db.commit()
        done[meta.table] = count
    return done
