# Teams and organizations

Most products are used by teams: a company signs up, invites colleagues,
and everyone in it shares the same projects, while other companies never
see them. PyWeb builds this in.

```python
from pyweb import App, Field, Model, server

app = App(database="sqlite:///app.db")
auth = app.use_auth(orgs=True)


class Project(Model, tenant=True):
    name: str = Field(max=60)


@server(login=True)
def add_project(name: str) -> int:
    return Project.create(name=name).id          # belongs to the user's current organization


@server(login=True)
def projects() -> list:
    return [p.name for p in Project.query()]     # only that organization's projects
```

That's all an app needs: the organization pages, invitations and the
isolation come with `orgs=True` and `tenant=True`.

## Organizations, members and roles

`app.use_auth(orgs=True)` adds three tables (`organizations`,
`org_memberships`, `org_invitations`) and these pages:

| Route | What it does |
|---|---|
| `/orgs` | The organizations you belong to, switching between them, and creating one. |
| `/orgs/<slug>` | Members and their roles, inviting by email, pending invitations, removing members, leaving, and handing over ownership. Only members can open it; for anyone else it doesn't exist (404). |
| `/invite/<token>` | Accepting an invitation (after signing in with the invited email address). |

Roles:

| Role | Can |
|---|---|
| `owner` | everything, including making other owners and handing over ownership |
| `admin` | invite, change roles (except owner) and remove members (except owners) |
| `member` | read and write the organization's data |
| `viewer` | read only |

An organization always keeps at least one owner: the last owner can't
leave or become something else until they make someone else owner.
Invitations are emailed, work once, expire after 7 days, and are only
for the address they were sent to. Only a hash of the link is stored.

The kit's Python API does the same for your own pages:

| Call | Effect |
|---|---|
| `auth.org()`, `auth.membership()`, `auth.role()` | The organization the signed-in user works in, their membership, their role. |
| `auth.require_org("admin", "owner")` | The organization, or raise (401 / 403) without one or without the role. |
| `auth.orgs(user)` | `[(membership, organization)]` for a user. |
| `auth.switch_org(org)` | Work in another organization the user belongs to (kept in the session). |
| `auth.create_org(name)` | A new organization with the signed-in user as owner. |
| `auth.invite(org, email, role)`, `auth.accept_invite(token)` | Invitations. |
| `auth.set_role(org, user, role)`, `auth.remove_member(org, user)`, `auth.transfer_ownership(org, user)` | Managing members (owners and admins). |
| `auth.members(org)` | `[(membership, user)]`, owners first. |

## Guards and plans

```python
@server(org_role="admin")                          # or a list: ["owner", "admin"]
def rename_org(name: str): ...

@server(org_role=["owner", "admin"], feature="exports")
def export_everything(): ...

@server(rate="100/hour", per="org")                # one budget for the whole organization
def generate_report(): ...
```

`org_role=` needs that role in the user's current organization, and
`feature=` needs the organization's plan to include the feature; both
answer 403 with a message saying which. Plans are a dictionary in your
code:

```python
auth.plans({
    "free": {"seats": 3, "exports": False},
    "pro": {"seats": 50, "exports": True},
})
```

`True`/`False` values are features (`auth.feature("exports")`), numbers
are limits (`auth.limit("seats")`). `seats` is enforced when inviting.
Organizations start on `free`; your billing code changes `org.plan`. PyWeb
doesn't take payments itself. Until you call `auth.plans()`, every feature
is included.

## Tenant Models

`class Project(Model, tenant=True)` adds an `org` column (a foreign key to
the organization, indexed, never accepted from a browser). While code runs
for an organization:

- every query on the Model, including `get()`, relations loaded with
  `include()`, counts, `live()` lists and bulk `update()`/`delete()`, only
  sees that organization's rows;
- rows it creates belong to that organization;
- saving or deleting a row of another organization fails with `404` (the
  row doesn't exist as far as this user is concerned), even if the code
  loaded it some other way;
- a bulk `update()` can't move rows to another organization;
- members with the `viewer` role can't write (`403`).

These rules combine with your own [row policies](10-auth.md#row-policies):
both apply.

## On Postgres, the database enforces it too

On Postgres, `pyweb db diff` adds row-level security to the migration of
every tenant table: `ENABLE` and `FORCE ROW LEVEL SECURITY` and a policy
comparing `org_id` with a setting PyWeb puts on the connection before each
statement that touches the table. So even raw SQL that forgets the
`org_id` condition, or SQL injected into a query, only sees and writes the
current organization's rows:

```python
with org_scope(acme):
    db.execute("SELECT * FROM projects").fetchall()   # Acme's projects only, filtered by Postgres
```

Two things to know:

- Superusers and roles with `BYPASSRLS` skip every policy. Run the app as
  an ordinary role (the one that owns the tables is fine: `FORCE` applies
  the policy to the owner too).
- Code outside any organization (migrations, scripts) sees every row, as
  with the ORM.

SQLite and MySQL have no row-level security; there the ORM's scoping is
the protection.

## Where "the current organization" comes from

- **A request:** the signed-in user's current organization (the one they
  last switched to, or their first).
- **A background job:** the organization of the request that queued it.
  A job queued while working in Acme runs for Acme, so it can't read
  another organization's rows even though no one is signed in. The same
  arguments queued by two organizations are two different jobs.
- **`with org_scope(org):`** in scripts, cron jobs and tests.

```python
from pyweb.authkit.orgs import Organization
from pyweb.jobs import cron
from pyweb.tenancy import org_scope

@cron("0 7 * * *")
def daily_digest():
    for org in Organization.query():
        with org_scope(org):
            send_digest(org, Project.query().all())    # this organization's projects only
```

Code outside all three (migrations, seeds, scripts and cron jobs that
don't open a scope) isn't limited to one organization, the same as row
policies outside requests. `with system():` turns scoping off on purpose
(admin tools).

## Testing

```python
from pyweb.testing import TestClient

def test_projects_stay_private():
    ann, bo = TestClient("app.pyweb"), TestClient("app.pyweb")
    ann.login("ann@example.com", org="Acme")            # a member (here the owner) of Acme
    bo.login("bo@example.com", org="Globex")
    ann.rpc("add_project", name="Rocket")              # your own server functions
    assert bo.rpc("projects") == []
    bo.login("bo@example.com", org="Acme", org_role="viewer")   # now Bo joins Acme too
    assert [p["name"] for p in bo.rpc("projects")] == ["Rocket"]
```

`login(..., org=...)` creates the organization if needed, adds the
membership with `org_role` (default `"owner"`) and switches to it.
`pyweb.testing.as_org(org)` runs test code for an organization, and
`Factory(...).for_org(org)` makes tenant rows in one. See
[Testing](11-testing.md#apps-with-teams).

PyWeb's own test suite runs random sequences of reads, writes, deletes
and bulk updates by several organizations and checks that none of them
ever sees or changes another organization's rows.

With an AI assistant, `pyweb_render` and `pyweb_call` take `as_user` and
`org` to act as a member, `pyweb_teams` lists each organization's
members, invitations and rows, and the `add_teams` prompt walks through
adding teams to an existing app (see [AI assistants & MCP](17-ai-assistants.md)).
