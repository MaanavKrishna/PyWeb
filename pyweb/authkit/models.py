"""The auth kit's tables: users, linked sign-in providers, passkeys, one-time
tokens and the audit log. Ordinary Models: query them, migrate them,
show them in /admin."""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets

from pyweb.models import Email, Field, ForeignKey, Index, Model


class User(Model):
    email: Email = Field(unique=True, max=254)
    name: str = Field("", max=80)
    password_hash: str | None = Field(None, private=True)
    email_verified: bool = False
    roles: list = Field(default_factory=list)
    is_active: bool = True
    totp_secret: str | None = Field(None, private=True)
    recovery_codes: list | None = Field(None, private=True)
    session_version: int = Field(0, readonly=True)
    created_at: dt.datetime = Field(auto_now_add=True)
    last_login_at: dt.datetime | None = Field(None, readonly=True)

    class Meta:
        table = "users"

    def __str__(self):
        return self.name or self.email

    def has_role(self, *roles):
        mine = set(self.roles or [])
        return all(r in mine for r in roles)

    @property
    def two_factor(self):
        return bool(self.totp_secret)


class Identity(Model):
    """A sign-in through another service (GitHub, Google, Microsoft)."""

    user: User = ForeignKey(related_name="identities")
    provider: str = Field(max=40)
    subject: str = Field(max=255)
    email: str | None = Field(None, max=254)
    created_at: dt.datetime = Field(auto_now_add=True)

    class Meta:
        table = "auth_identities"
        indexes = [Index("provider", "subject", unique=True)]


class Credential(Model):
    """A passkey (WebAuthn credential)."""

    user: User = ForeignKey(related_name="passkeys")
    credential_id: str = Field(unique=True, max=512)
    public_key: bytes = Field(private=True)
    algorithm: int = -7
    sign_count: int = 0
    name: str = Field("Passkey", max=80)
    created_at: dt.datetime = Field(auto_now_add=True)
    last_used_at: dt.datetime | None = None

    class Meta:
        table = "auth_credentials"


class AuthToken(Model):
    """A one-time link (sign-up confirmation, password reset, magic sign-in).

    Only a hash of the token is stored, so a database leak doesn't leak working links.
    """

    user: User | None = ForeignKey(related_name="auth_tokens", on_delete="cascade", nullable=True)
    purpose: str = Field(max=20)
    token_hash: str = Field(unique=True, max=64, private=True)
    email: str | None = Field(None, max=254)
    data: dict | None = Field(None, private=True)
    expires_at: dt.datetime
    used_at: dt.datetime | None = None
    created_at: dt.datetime = Field(auto_now_add=True)

    class Meta:
        table = "auth_tokens"

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    @classmethod
    def issue(cls, purpose, *, user=None, email=None, data=None, ttl=900):
        token = secrets.token_urlsafe(32)
        if secrets.randbelow(100) == 0:                    # now and then, clear out old links
            cls.where(expires_at__lt=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).delete()
        cls.create(user=user, purpose=purpose, token_hash=cls.digest(token), email=email, data=data,
                   expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ttl))
        return token

    @classmethod
    def redeem(cls, purpose, token):
        """The token's row if valid (and marks it used), else None. Each link works once."""
        if not token or len(token) > 200:
            return None
        row = cls.where(purpose=purpose, token_hash=cls.digest(token)).first()
        now = dt.datetime.now(dt.timezone.utc)
        if row is None or row.used_at is not None or row.expires_at < now:
            return None
        claimed = cls.where(id=row.id, used_at__isnull=True).update(used_at=now)
        return row if claimed == 1 else None          # two clicks at once: only one wins


class AuthEvent(Model):
    """The audit log: sign-ins, failures, resets, 2FA and role changes."""

    user: User | None = ForeignKey(related_name="auth_events", on_delete="set null")
    kind: str = Field(max=40)
    ok: bool = True
    email: str | None = Field(None, max=254)
    ip: str | None = Field(None, max=64)
    user_agent: str | None = Field(None, max=300)
    detail: str | None = Field(None, max=300)
    created_at: dt.datetime = Field(auto_now_add=True)

    class Meta:
        table = "auth_events"
        ordering = ["-id"]
        indexes = [Index("user", "created_at")]


MODELS = [User, Identity, Credential, AuthToken, AuthEvent]
