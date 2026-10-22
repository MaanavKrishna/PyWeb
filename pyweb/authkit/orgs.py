"""Organizations: teams that share data. ``app.use_auth(orgs=True)`` turns them on.

* :class:`Organization` (name, a unique slug, a plan);
* :class:`Membership` links a user to an organization with a role: ``owner``,
  ``admin``, ``member`` or ``viewer``;
* :class:`Invitation` is an emailed invite (the token is stored only as a hash).

Models declared ``tenant=True`` belong to an organization; see :mod:`pyweb.tenancy`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import secrets

from pyweb.models import Field, ForeignKey, Index, Model

from .models import User

ROLES = ("owner", "admin", "member", "viewer")
MANAGE = ("owner", "admin")              # may invite, change roles and remove members
INVITE_DAYS = 7


class Organization(Model):
    name: str = Field(min=2, max=80)
    slug: str = Field(unique=True, max=48, pattern=r"[a-z0-9]+(-[a-z0-9]+)*",
                      message="lowercase letters, digits and dashes")
    plan: str = Field("free", max=40, readonly=True)
    created_at: dt.datetime = Field(auto_now_add=True)

    class Meta:
        table = "organizations"

    def __str__(self):
        return self.name


class Membership(Model):
    org: Organization = ForeignKey(related_name="memberships")
    user: User = ForeignKey(related_name="memberships")
    role: str = Field("member", choices=list(ROLES))
    created_at: dt.datetime = Field(auto_now_add=True)

    class Meta:
        table = "org_memberships"
        indexes = [Index("org", "user", unique=True)]


class Invitation(Model):
    org: Organization = ForeignKey(related_name="invitations")
    email: str = Field(max=254)
    role: str = Field("member", choices=list(ROLES))
    token_hash: str = Field(unique=True, max=64)
    invited_by: User | None = ForeignKey(related_name="sent_invitations", on_delete="set null")
    created_at: dt.datetime = Field(auto_now_add=True)
    expires_at: dt.datetime
    accepted_at: dt.datetime | None = None

    class Meta:
        table = "org_invitations"

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    @classmethod
    def issue(cls, org, email, role="member", invited_by=None, days=INVITE_DAYS):
        """A new invitation; returns ``(row, token)``. Only the token's hash is stored."""
        token = secrets.token_urlsafe(32)
        row = cls.create(org=org, email=email.strip().lower(), role=role, token_hash=cls.digest(token),
                         invited_by=invited_by,
                         expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days))
        return row, token

    @classmethod
    def find(cls, token):
        """The pending, unexpired invitation for ``token``, or None."""
        if not token or len(token) > 200:
            return None
        row = cls.where(token_hash=cls.digest(token)).first()
        if row is None or row.accepted_at is not None or row.expires_at < dt.datetime.now(dt.timezone.utc):
            return None
        return row


MODELS = [Organization, Membership, Invitation]


def slugify(text, taken=()):
    """A URL-safe slug for ``text`` that isn't in ``taken``."""
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "team"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug
