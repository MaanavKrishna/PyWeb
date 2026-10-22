"""Your data and your account: ``auth.export_user(user)`` and ``auth.delete_user(user)``.

The export holds the account and every row of every Model that points at the
user (found through foreign keys), as JSON. Encrypted fields are included,
decrypted (it is the user's own data); private fields such as password hashes
and two-factor secrets are not.

Deleting an account removes the user. Rows that point at them follow their
foreign key's ``on_delete`` rule (``cascade`` deletes them, ``set null`` keeps
them without the user). Organizations the user owns alone are deleted with
them; one that has other members needs a new owner first. The audit log keeps
its entries, without the email address, under a pseudonymous id.
"""

from __future__ import annotations

import datetime as dt
import hashlib

from pyweb.rules import ValidationError


def _system():
    from pyweb.models import system
    return system()


def _row(obj):
    """A row as plain data: every column except private ones (encrypted ones decrypted)."""
    meta = type(obj)._meta
    return {f.name: obj.__dict__.get(f.name) for f in meta.fields.values() if not f.private}


def _pointing_at(target):
    """``[(model, field)]``: every foreign key column that points at ``target``'s table."""
    from pyweb.models import all_models
    out = []
    for model in all_models():
        for rel in model._meta.relations.values():
            if rel.target is target or getattr(rel.target, "_meta", None) is target._meta:
                out.append((model, rel.field))
    return out


class PrivacyMixin:
    def export_user(self, user):
        """Everything stored about ``user``, as JSON-ready data."""
        from .models import User
        with _system():
            data = {"exported_at": dt.datetime.now(dt.timezone.utc).isoformat(), "account": _row(user),
                    "rows": {}}
            for model, field in _pointing_at(User):
                rows = [_row(r) for r in model.query().where(field == user.id).order("id")]
                if rows:
                    data["rows"].setdefault(model._meta.table, []).extend(rows)
        return data

    def delete_user(self, user):
        """Delete ``user`` and what belongs only to them; see the module docs. Raises ValidationError
        (nothing changed) while they're the only owner of an organization that has other members."""
        orgs_alone = []
        if self.orgs_enabled:
            from .orgs import Membership, Organization
            with _system():
                for m in Membership.where(user=user, role="owner"):
                    owners = Membership.where(org_id=m.org_id, role="owner").count()
                    members = Membership.where(org_id=m.org_id).count()
                    if owners == 1 and members > 1:
                        org = Organization.get(m.org_id)
                        raise ValidationError({"__all__": f"You're the only owner of {org.name}: make someone "
                                                          "else owner first, or remove the other members."})
                    if members == 1:
                        orgs_alone.append(m.org_id)
        pseudonym = "user-" + hashlib.sha256(f"{user.id}:{user.email}".encode()).hexdigest()[:12]
        from .models import AuthEvent, User
        db = User._database()
        with db.transaction(), _system():
            AuthEvent.where(user=user).update(email=None, user_agent=None, detail=pseudonym)
            if orgs_alone:
                from .orgs import Organization
                Organization.where(Organization.id.in_(orgs_alone)).delete()
            User.where(id=user.id).delete()
            AuthEvent.create(user=None, kind="account_deleted", detail=pseudonym)
        return pseudonym
