"""The auth kit's organization API (``app.use_auth(orgs=True)``): mixed into :class:`AuthKit`."""

from __future__ import annotations

import datetime as dt

from pyweb.rules import ValidationError

from .orgs import MANAGE, ROLES, Invitation, Membership, Organization, slugify


def _system():
    from pyweb.models import system
    return system()


def _now():
    return dt.datetime.now(dt.timezone.utc)


class TeamsMixin:
    orgs_enabled = False

    def _enable_orgs(self):
        from pyweb import tenancy
        from .orgs import MODELS as ORG_MODELS
        self.orgs_enabled = True
        self.app.models.extend(m for m in ORG_MODELS if m not in self.app.models)
        tenancy.request_resolver = self._org_context

    # ------------------------------------------------------------- the current organization
    def _org_context(self):
        """``(in_request, org_id, role)`` for :mod:`pyweb.tenancy`."""
        from pyweb.context import current
        try:
            ctx = current()
        except RuntimeError:
            return False, None, None
        cached = getattr(ctx, "_pw_org", None)
        if cached is None:
            cached = ctx._pw_org = self._resolve_org()
        org, membership = cached
        return True, (org.id if org else None), (membership.role if membership else None)

    def _resolve_org(self):
        from pyweb.context import session
        user = self.user()
        if user is None:
            return None, None
        wanted = (session.user() or {}).get("org")
        with _system():
            mine = Membership.where(user=user).order("id").all()
            if not mine:
                return None, None
            chosen = next((m for m in mine if m.org_id == wanted), mine[0])
            return Organization.get(chosen.org_id), chosen

    def org(self):
        """The organization the signed-in user is working in, or None."""
        from pyweb.context import current
        try:
            ctx = current()
        except RuntimeError:
            return None
        if getattr(ctx, "_pw_org", None) is None:
            ctx._pw_org = self._resolve_org()
        return ctx._pw_org[0]

    def membership(self):
        """The signed-in user's :class:`Membership` in :meth:`org`, or None."""
        self.org()
        from pyweb.context import current
        return current()._pw_org[1] if self.user() else None

    def role(self):
        m = self.membership()
        return m.role if m else None

    def orgs(self, user=None):
        """``[(membership, organization)]`` for ``user`` (default: the signed-in user), oldest first."""
        user = user or self.user()
        if user is None:
            return []
        with _system():
            mine = Membership.where(user=user).order("id").all()
            orgs = {o.id: o for o in Organization.where(Organization.id.in_([m.org_id for m in mine]))}
        return [(m, orgs[m.org_id]) for m in mine if m.org_id in orgs]

    def switch_org(self, org):
        """Work in ``org`` from now on (the user must be a member); returns the membership."""
        from pyweb.context import current, session
        user = self.user()
        org_id = getattr(org, "id", org)
        with _system():
            m = Membership.where(user=user, org_id=org_id).first() if user else None
        if m is None:
            raise ValidationError({"__all__": "You aren't a member of that organization."})
        session.update(org=org_id)
        with _system():
            current()._pw_org = (Organization.get(org_id), m)
        return m

    def require_org(self, *roles):
        """The current organization, or raise (401 not signed in, 403 no organization or missing role)."""
        from pyweb.rpc import Code, RPCError
        if self.user() is None:
            raise RPCError(Code.AUTH, "sign in first")
        org = self.org()
        if org is None:
            raise RPCError(Code.FORBIDDEN, "create or join an organization first")
        if roles and self.role() not in roles:
            raise RPCError(Code.FORBIDDEN, f"this needs the {' or '.join(roles)} role")
        return org

    # ------------------------------------------------------------- managing organizations
    def create_org(self, name, owner=None, slug=None):
        """A new organization with ``owner`` (default: the signed-in user) as its owner."""
        owner = owner or self.user()
        if owner is None:
            raise ValidationError({"__all__": "Sign in first."})
        with _system():
            taken = {o.slug for o in Organization.query().all()} if slug is None else set()
            org = Organization.create(name=(name or "").strip(), slug=slug or slugify(name, taken))
            Membership.create(org=org, user=owner, role="owner")
        self.event("org_created", user=owner, detail=org.slug)
        return org

    def _manager(self, org):
        """The signed-in user's membership in ``org`` if they may manage it, else raise."""
        from pyweb.rpc import Code, RPCError
        user = self.user()
        with _system():
            m = Membership.where(user=user, org_id=org.id).first() if user else None
        if m is None:
            raise RPCError(Code.NOT_FOUND, "no such organization")      # not a member: don't confirm it exists
        if m.role not in MANAGE:
            raise RPCError(Code.FORBIDDEN, "only owners and admins can do this")
        return m

    def members(self, org):
        """``[(membership, user)]`` of ``org``, owners first."""
        from .models import User
        with _system():
            ms = Membership.where(org_id=org.id).order("id").all()
            users = {u.id: u for u in User.where(User.id.in_([m.user_id for m in ms]))}
        order = {r: i for i, r in enumerate(ROLES)}
        return sorted(((m, users[m.user_id]) for m in ms if m.user_id in users),
                      key=lambda mu: (order.get(mu[0].role, 9), mu[0].id))

    def invite(self, org, email, role="member"):
        """Email an invitation to join ``org`` (by an owner or admin); returns the Invitation."""
        from pyweb.rules import EMAIL_RE
        me = self._manager(org)
        email = (email or "").strip().lower()
        if not EMAIL_RE.match(email):
            raise ValidationError({"email": "isn't an email address"})
        if role not in ROLES or (role == "owner" and me.role != "owner"):
            raise ValidationError({"role": "isn't a role you can give"})
        with _system():
            from .models import User
            existing = User.where(email=email).first()
            if existing and Membership.where(org_id=org.id, user=existing).exists():
                raise ValidationError({"email": "is already a member"})
            inv, token = Invitation.issue(org, email, role, invited_by=self.user())
        link = self._link(f"/invite/{token}")
        self._mail(email, f"Join {org.name}",
                   f"{self.user().name or self.user().email} invited you to {org.name} on {self.site_name}.\n\n"
                   f"Accept: {link}\n\nThe link works for 7 days. If you didn't expect it, ignore this email.")
        self.event("org_invited", user=self.user(), email=email, detail=f"{org.slug}:{role}")
        return inv

    def accept_invite(self, token, user=None):
        """Join the organization of the invitation ``token``; returns the Membership (or None if the
        invitation is unknown, used or expired). The invitation's email must be the user's."""
        user = user or self.user()
        with _system():
            inv = Invitation.find(token)
            if inv is None or user is None:
                return None
            if inv.email != (user.email or "").lower():
                raise ValidationError({"__all__": f"This invitation is for {inv.email}. Sign in with that "
                                                  "address to accept it."})
            claimed = Invitation.where(id=inv.id, accepted_at__isnull=True).update(accepted_at=_now())
            if claimed != 1:
                return None                                   # accepted twice at once: only one wins
            m = Membership.where(org_id=inv.org_id, user=user).first()
            if m is None:
                m = Membership.create(org_id=inv.org_id, user=user, role=inv.role)
        self.event("org_joined", user=user, detail=str(inv.org_id))
        return m

    def set_role(self, org, user, role):
        me = self._manager(org)
        if role not in ROLES:
            raise ValidationError({"role": "isn't a role"})
        with _system():
            m = Membership.where(org_id=org.id, user=user).first()
            if m is None:
                raise ValidationError({"__all__": "Not a member."})
            if "owner" in (role, m.role) and me.role != "owner":
                raise ValidationError({"__all__": "Only owners can make or unmake owners."})
            if m.role == "owner" and role != "owner" and self._owners(org) == 1:
                raise ValidationError({"__all__": "An organization needs an owner: make someone else owner "
                                                  "first."})
            m.role = role
            m.save()
        self.event("org_role_changed", user=self.user(), detail=f"{org.slug}:{user.id}:{role}")
        return m

    def _owners(self, org):
        with _system():
            return Membership.where(org_id=org.id, role="owner").count()

    def remove_member(self, org, user):
        """Remove ``user`` from ``org`` (managers), or leave it (``user`` is yourself)."""
        me_user = self.user()
        if user.id != getattr(me_user, "id", None):
            me = self._manager(org)
        else:
            me = None
        with _system():
            m = Membership.where(org_id=org.id, user=user).first()
            if m is None:
                return False
            if m.role == "owner" and me is not None and me.role != "owner":
                raise ValidationError({"__all__": "Only owners can remove an owner."})
            if m.role == "owner" and self._owners(org) == 1:
                raise ValidationError({"__all__": "The last owner can't leave: make someone else owner first, "
                                                  "or delete the organization."})
            m.delete()
        self.event("org_left" if me is None else "org_member_removed", user=me_user,
                   detail=f"{org.slug}:{user.id}")
        return True

    def transfer_ownership(self, org, user):
        """Make ``user`` (a member) an owner and the current owner an admin."""
        me = self._manager(org)
        if me.role != "owner":
            raise ValidationError({"__all__": "Only an owner can hand over ownership."})
        with _system():
            target = Membership.where(org_id=org.id, user=user).first()
            if target is None:
                raise ValidationError({"__all__": "Not a member."})
            target.role = "owner"
            target.save()
            me.role = "admin"
            me.save()
        self.event("org_transferred", user=self.user(), detail=f"{org.slug}:{user.id}")
        return target
