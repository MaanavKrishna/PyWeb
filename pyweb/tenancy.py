"""Which organization the running code acts for: tenant Models are scoped to it.

``class Project(Model, tenant=True)`` gets an ``org`` foreign key, and while
code runs for an organization every query on it is limited to that
organization's rows, every row it creates belongs to it, and saving a row of
another organization is refused. Members with the ``viewer`` role can read
but not write.

Where the organization comes from:

* **a request**: the signed-in user's current organization (the auth kit
  keeps it in the session; see ``auth.org()`` and ``auth.switch_org()``);
* **a job**: the organization of the request (or job) that queued it;
* **``with org_scope(org):``** in scripts, cron jobs and tests.

Code outside all three (migrations, seeds, scripts, cron jobs that loop over
every organization) isn't scoped, the same as row policies; ``with
system():`` turns scoping off on purpose.
"""

from __future__ import annotations

import contextvars

_UNSET = object()
_SCOPE: contextvars.ContextVar = contextvars.ContextVar("pyweb_org_scope", default=_UNSET)

#: Set by the auth kit: ``() -> (in_request, org_id or None, role or None)`` for the current request.
request_resolver = None

WRITE_ROLES = ("owner", "admin", "member")


class org_scope:
    """``with org_scope(org):`` (an Organization or its id) runs code for that organization;
    ``org_scope(None)`` runs it for no organization (tenant Models then show no rows)."""

    def __init__(self, org, role=None):
        self.org_id = getattr(org, "id", org)
        self.role = role

    def __enter__(self):
        self._token = _SCOPE.set((self.org_id, self.role))
        return self

    def __exit__(self, *exc):
        _SCOPE.reset(self._token)
        return False


def current():
    """``(scoped, org_id, role)``: ``scoped`` is False when no organization context applies."""
    from .models import _SYSTEM
    if _SYSTEM.get():
        return False, None, None
    explicit = _SCOPE.get()
    if explicit is not _UNSET:
        return True, explicit[0], explicit[1]
    if request_resolver is not None:
        in_request, org_id, role = request_resolver()
        if in_request:
            return True, org_id, role
    return False, None, None


def current_org_id():
    """The id of the organization the code runs for, or None."""
    scoped, org_id, _ = current()
    return org_id if scoped else None


def read_condition(model):
    """The condition limiting ``model`` (a tenant Model) to the current organization, or None."""
    scoped, org_id, _ = current()
    if not scoped:
        return None
    field = model._meta.field("org_id")
    if org_id is None:
        from .models.query import Raw
        return Raw("1 = 0")
    return field == org_id


def before_write(row, action):
    """Set a new row's organization, and refuse writes outside the current one (or by viewers)."""
    scoped, org_id, role = current()
    if not scoped:
        return
    from .rpc import Code, RPCError
    kind = type(row).__name__.lower()
    if org_id is None:
        raise RPCError(Code.FORBIDDEN, f"choose an organization before you {action} a {kind}")
    if role is not None and role not in WRITE_ROLES:
        raise RPCError(Code.FORBIDDEN, f"your role ({role}) can't {action} a {kind}")
    current_value = row.__dict__.get("org_id")
    if current_value is None and action == "create":
        row.__dict__["org_id"] = org_id
        return
    if current_value != org_id:
        # Another organization's row: say it doesn't exist rather than confirm it does.
        raise RPCError(Code.NOT_FOUND, f"no such {kind}")


def job_payload():
    """What a queued job carries so it runs for the same organization."""
    scoped, org_id, _ = current()
    return {"org": org_id} if scoped and org_id is not None else {}


def before_bulk(model, action, values=None):
    """``Model.where(...).update()`` / ``.delete()``: the same rules as for one row."""
    scoped, org_id, role = current()
    if not scoped:
        return
    from .rpc import Code, RPCError
    kind = model.__name__.lower()
    if org_id is None:
        raise RPCError(Code.FORBIDDEN, f"choose an organization before you {action} {kind} rows")
    if role is not None and role not in WRITE_ROLES:
        raise RPCError(Code.FORBIDDEN, f"your role ({role}) can't {action} {kind} rows")
    for key in ("org", "org_id"):
        if values and key in values and getattr(values[key], "id", values[key]) != org_id:
            raise RPCError(Code.FORBIDDEN, f"{kind} rows can't be moved to another organization")
