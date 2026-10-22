"""Organization pages (``app.use_auth(orgs=True)``): ``/orgs``, ``/orgs/<slug>`` and ``/invite/<token>``.
Plain forms that work without JavaScript, like the rest of the auth kit."""

from __future__ import annotations

import re
import urllib.parse

from pyweb.rpc import RPCError
from pyweb.rules import ValidationError

from . import html as H
from .orgs import MANAGE, ROLES, Invitation, Membership, Organization
from .pages import forbidden, page, redirect


def _system():
    from pyweb.models import system
    return system()


def _hidden(name, value):
    return f'<input type="hidden" name="{H.esc(name)}" value="{H.esc(str(value))}">'


def _role_select(name, current, allowed, form="f"):
    opts = "".join(f'<option value="{r}"{" selected" if r == current else ""}>{r}</option>' for r in allowed)
    return f'<select name="{name}" aria-label="Role" form="{form}">{opts}</select>'


def _errors(exc):
    return "; ".join(v for v in exc.errors.values())


def _sign_in_first(c):
    return redirect("/login?" + urllib.parse.urlencode({"next": c.path}))


# ------------------------------------------------------------------ /orgs

def orgs(c):
    kit = c.kit
    user = kit.user()
    if user is None:
        return _sign_in_first(c)
    msg, err = "", ""
    if c.method == "POST":
        if not c.csrf_ok():
            return forbidden(c)
        try:
            if c.get("action") == "create":
                org = kit.create_org(c.get("name"))
                kit.switch_org(org)
                return redirect(f"/orgs/{org.slug}")
            if c.get("action") == "switch":
                kit.switch_org(int(c.get("id") or 0))
                return redirect(kit.after_login or "/")
        except ValidationError as exc:
            err = _errors(exc)
        except ValueError:
            err = "That isn't an organization."
    current = kit.org()
    rows = ""
    for m, org in kit.orgs(user):
        here = current is not None and org.id == current.id
        action = '<span class="pw-muted">Current</span>' if here else H.form(
            "/orgs", _hidden("action", "switch") + _hidden("id", org.id) + H.button("Switch", kind="secondary pw-inline"),
            csrf=c.csrf)
        rows += (f'<tr><td><a href="/orgs/{H.esc(org.slug)}">{H.esc(org.name)}</a></td><td>{H.esc(m.role)}</td>'
                 f"<td>{action}</td></tr>")
    body = H.notice(msg, "success") + H.notice(err, "error")
    body += f'<table class="pw-table">{rows}</table>' if rows else '<p class="pw-muted">You aren\'t in any yet.</p>'
    body += "<h2>New organization</h2>" + H.form("/orgs", _hidden("action", "create") +
                                                  H.field("name", "Name", form="new", minlength=2, maxlength=80) +
                                                  H.button("Create"), csrf=c.csrf)
    return page(c, "Organizations", body, wide=True, status=422 if err else 200)


# ------------------------------------------------------------------ /orgs/<slug>

def org_settings(c, slug):
    kit = c.kit
    user = kit.user()
    if user is None:
        return _sign_in_first(c)
    with _system():
        org = Organization.where(slug=slug).first()
        mine = Membership.where(org_id=org.id, user=user).first() if org else None
    if org is None or mine is None:
        # Not a member: the same answer as for an organization that doesn't exist.
        return page(c, "Not found", H.notice("No such organization.", "error"), status=404)
    msg, err = "", ""
    if c.method == "POST":
        if not c.csrf_ok():
            return forbidden(c)
        try:
            msg = _settings_action(c, kit, org, user)
            if msg is None:
                return redirect("/orgs")                     # left the organization
        except ValidationError as exc:
            err = _errors(exc)
        except RPCError as exc:
            err = str(exc)
        except ValueError:
            err = "Something in the form wasn't right."
        with _system():
            mine = Membership.where(org_id=org.id, user=user).first()
            if mine is None:
                return redirect("/orgs")
    manage = mine.role in MANAGE
    body = H.notice(msg, "success") + H.notice(err, "error")
    rows = ""
    for m, member in kit.members(org):
        cells = f"<td>{H.esc(member.name or member.email)}<br><small>{H.esc(member.email)}</small></td>"
        if manage and member.id != user.id:
            allowed = ROLES if mine.role == "owner" else [r for r in ROLES if r != "owner"]
            fid = f"role-{m.id}"
            role_cell = (H.form(f"/orgs/{org.slug}", _hidden("action", "role") + _hidden("user", member.id) +
                                _role_select("role", m.role, allowed, form=fid) + " " +
                                H.button("Change", kind="secondary pw-inline"), csrf=c.csrf,
                                extra=f' id="{fid}"'))
            remove = H.form(f"/orgs/{org.slug}", _hidden("action", "remove") + _hidden("user", member.id) +
                            H.button("Remove", kind="danger pw-inline"), csrf=c.csrf)
        else:
            role_cell, remove = H.esc(m.role), ""
            if member.id == user.id:
                remove = H.form(f"/orgs/{org.slug}", _hidden("action", "leave") +
                                H.button("Leave", kind="danger pw-inline"), csrf=c.csrf)
        rows += f"<tr>{cells}<td>{role_cell}</td><td>{remove}</td></tr>"
    body += f'<h2>Members</h2><table class="pw-table pw-members">{rows}</table>'
    if manage:
        allowed = ROLES if mine.role == "owner" else [r for r in ROLES if r != "owner"]
        opts = "".join(f'<option value="{r}"{" selected" if r == "member" else ""}>{r}</option>' for r in allowed)
        body += "<h2>Invite someone</h2>" + H.form(
            f"/orgs/{org.slug}", _hidden("action", "invite") + H.field("email", "Email", type="email", form="inv") +
            f'<div class="pw-field"><label for="pw-inv-role">Role</label>'
            f'<select id="pw-inv-role" name="role">{opts}</select></div>' + H.button("Send invitation"), csrf=c.csrf)
        with _system():
            pending = [i for i in Invitation.where(org_id=org.id, accepted_at__isnull=True).order("-id").limit(20)]
        if pending:
            rows = "".join(f"<tr><td>{H.esc(i.email)}</td><td>{H.esc(i.role)}</td><td>until "
                           f"{i.expires_at:%Y-%m-%d}</td><td>" +
                           H.form(f"/orgs/{org.slug}", _hidden("action", "cancel_invite") + _hidden("id", i.id) +
                                  H.button("Cancel", kind="secondary pw-inline"), csrf=c.csrf) + "</td></tr>"
                           for i in pending)
            body += f'<h3>Waiting for an answer</h3><table class="pw-table">{rows}</table>'
    if mine.role == "owner":
        others = [(m, u) for m, u in kit.members(org) if u.id != user.id]
        if others:
            opts = "".join(f'<option value="{u.id}">{H.esc(u.name or u.email)}</option>' for _, u in others)
            body += "<h2>Hand over ownership</h2>" + H.form(
                f"/orgs/{org.slug}", _hidden("action", "transfer") +
                f'<div class="pw-field"><label for="pw-transfer">New owner</label>'
                f'<select id="pw-transfer" name="user">{opts}</select></div>' +
                H.button("Make them owner", kind="secondary"), csrf=c.csrf)
    body += '<p class="pw-links"><a href="/orgs">All organizations</a></p>'
    return page(c, org.name, body, wide=True, lead=f"Your role: {mine.role}", status=422 if err else 200)


def _member(org, user_id):
    from .models import User
    with _system():
        m = Membership.where(org_id=org.id, user_id=int(user_id)).first()
        return User.get(m.user_id) if m else None


def _settings_action(c, kit, org, user):
    action = c.get("action")
    if action == "invite":
        kit.invite(org, c.get("email"), c.get("role") or "member")
        return f"Invitation sent to {c.get('email').strip().lower()}."
    if action == "cancel_invite":
        kit._manager(org)
        with _system():
            Invitation.where(id=int(c.get("id")), org_id=org.id, accepted_at__isnull=True).delete()
        return "Invitation cancelled."
    if action == "role":
        target = _member(org, c.get("user"))
        if target is None:
            raise ValidationError({"__all__": "Not a member."})
        kit.set_role(org, target, c.get("role"))
        return "Role changed."
    if action == "remove":
        target = _member(org, c.get("user"))
        if target is not None:
            kit.remove_member(org, target)
        return "Removed."
    if action == "leave":
        kit.remove_member(org, user)
        return None
    if action == "transfer":
        target = _member(org, c.get("user"))
        if target is None:
            raise ValidationError({"__all__": "Not a member."})
        kit.transfer_ownership(org, target)
        return f"{target.name or target.email} is now an owner; you're an admin."
    raise ValidationError({"__all__": "Unknown action."})


# ------------------------------------------------------------------ /invite/<token>

def accept(c, token):
    kit = c.kit
    user = kit.user()
    with _system():
        inv = Invitation.find(token)
        org = Organization.get(inv.org_id) if inv else None
    if inv is None or org is None:
        return page(c, "Invitation", H.notice("This invitation has expired or was already used. Ask for a new "
                                              "one.", "error"), status=404)
    if user is None:
        return redirect("/login?" + urllib.parse.urlencode({"next": c.path}))
    err = ""
    if c.method == "POST":
        if not c.csrf_ok():
            return forbidden(c)
        try:
            m = kit.accept_invite(token)
        except ValidationError as exc:
            m, err = None, _errors(exc)
        if m is not None:
            kit.switch_org(m.org_id)
            return redirect(kit.after_login or "/")
        err = err or "This invitation has expired or was already used."
    body = H.notice(err, "error") + H.form(c.path, H.button(f"Join {org.name}"), csrf=c.csrf)
    return page(c, f"Join {org.name}", body, lead=f"You were invited as {inv.role}.",
                status=422 if err else 200)


ROUTES = [
    (re.compile(r"^/orgs$"), orgs, ("GET", "POST")),
    (re.compile(r"^/orgs/(?P<slug>[a-z0-9-]{1,48})$"), org_settings, ("GET", "POST")),
    (re.compile(r"^/invite/(?P<token>[\w-]{20,100})$"), accept, ("GET", "POST")),
]
