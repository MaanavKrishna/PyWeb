"""API tokens: call server functions from scripts and other services.

``Authorization: Bearer pwk_...`` on a server function call acts as the token's
user (and organization, if it has one). A token only works for the functions in
its scopes (``["*"]`` for all), only for server function calls (never pages,
forms or the account), never counts as a recent sign-in (so ``fresh=`` functions
refuse it), and keeps working when the user signs out of their browser sessions.
Only a hash is stored; the token is shown once, when it's made.
"""

from __future__ import annotations

import datetime as dt
import secrets

from pyweb.rules import ValidationError

from .models import ApiToken, User

TOUCH_EVERY = 300          # seconds between last_used_at writes


def _system():
    from pyweb.models import system
    return system()


def _now():
    return dt.datetime.now(dt.timezone.utc)


class TokensMixin:
    def create_token(self, user, name, scopes=("*",), *, org=None, days=None):
        """A new token for ``user``; returns ``(row, token)``. ``scopes``: server function names or ``"*"``."""
        name = (name or "").strip()
        if not name:
            raise ValidationError({"name": "is required"})
        scopes = [s.strip() for s in scopes if s and s.strip()] or ["*"]
        token = ApiToken.PREFIX + secrets.token_urlsafe(32)
        org_id = getattr(org, "id", org)
        if org_id is not None and self.orgs_enabled:
            from .orgs import Membership
            with _system():
                if not Membership.where(org_id=org_id, user=user).exists():
                    raise ValidationError({"__all__": "You aren't a member of that organization."})
        with _system():
            row = ApiToken.create(user=user, org_id=org_id, name=name[:80], prefix=token[:10],
                                  token_hash=ApiToken.digest(token), scopes=scopes,
                                  expires_at=_now() + dt.timedelta(days=int(days)) if days else None)
        self.event("api_token_created", user=user, detail=f"{row.prefix}:{','.join(scopes)}")
        return row, token

    def tokens(self, user):
        with _system():
            return ApiToken.where(user=user, revoked_at__isnull=True).order("-id").all()

    def revoke_token(self, user, token_id):
        with _system():
            done = ApiToken.where(id=token_id, user=user, revoked_at__isnull=True).update(revoked_at=_now()) == 1
        if done:
            self.event("api_token_revoked", user=user, detail=str(token_id))
        return done

    def token_payload(self, raw, path):
        """The session payload for an ``Authorization: Bearer`` API token on ``path``, or None."""
        if not raw.startswith(ApiToken.PREFIX) or len(raw) > 200 or not path.startswith("/__pyweb/rpc/"):
            return None
        with _system():
            row = ApiToken.where(token_hash=ApiToken.digest(raw), revoked_at__isnull=True).first()
            if row is None or (row.expires_at is not None and row.expires_at < _now()):
                return None
            user = User.get(row.user_id)
            if user is None or not user.is_active:
                return None
            if row.last_used_at is None or (_now() - row.last_used_at).total_seconds() > TOUCH_EVERY:
                ApiToken.where(id=row.id).update(last_used_at=_now())
        payload = {"sub": user.id, "api": row.id, "scopes": list(row.scopes or []), "auth_time": 0,
                   "roles": list(user.roles or [])}
        if row.org_id is not None:
            payload["org"] = row.org_id
        return payload
