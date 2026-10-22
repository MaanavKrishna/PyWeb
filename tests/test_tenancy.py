"""Organizations and tenant Models: every query, save and job stays inside the current organization."""

import pytest

from pyweb import jobs, tenancy
from pyweb.models import system
from pyweb.testing import TestClient

APP = '''
from pyweb import App, Field, Model, server
from pyweb.tenancy import org_scope
from pyweb.models import system
from pyweb import jobs

app = App(database="sqlite:///{db}")
auth = app.use_auth(signup="instant", orgs=True)


class Project(Model, tenant=True):
    name: str = Field(max=60)


class Note(Model, tenant=True):
    text: str
    project: Project


@server(login=True)
def new_org(name: str) -> int:
    org = auth.create_org(name)
    auth.switch_org(org)
    return org.id


@server(login=True)
def switch(org_id: int) -> str:
    auth.switch_org(org_id)
    return auth.org().slug


@server(login=True)
def add_project(name: str) -> int:
    return Project.create(name=name).id


@server(login=True)
def projects() -> list:
    return [p.name for p in Project.query().order("id")]


@server(login=True)
def rename(project_id: int, name: str) -> str:
    p = Project.get_or_404(project_id)
    p.name = name
    p.save()
    return "ok"


@server(login=True)
def steal(project_id: int, org_id: int) -> str:
    with system():
        p = Project.get(project_id)
    p.name = "stolen"
    p.save()
    return "saved"


@server(login=True)
def move_all(org_id: int) -> int:
    return Project.where(Project.id > 0).update(org_id=org_id)


@server(login=True)
def invite(email: str, role: str = "member") -> int:
    return auth.invite(auth.org(), email, role).id


@server(login=True)
def accept(token: str) -> str:
    m = auth.accept_invite(token)
    auth.switch_org(m.org_id)
    return m.role


@server(login=True)
def count_later() -> int:
    return count_projects.enqueue().id and 1


@jobs.job
def count_projects():
    return len(Project.query().all())


@app.page("/")
def Home():
    <p>hi</p>
'''


@pytest.fixture
def app_file(tmp_path, monkeypatch):
    from pyweb import auth as pyauth
    from pyweb import models as M
    monkeypatch.setattr(M._state, "db", None)
    monkeypatch.setattr(pyauth, "_versions", None)
    monkeypatch.setenv("PYWEB_WORKER", "0")
    monkeypatch.setenv("PYWEB_BREACHED_PASSWORDS", "0")
    path = tmp_path / "app.pyweb"
    path.write_text(APP.replace("{db}", str(tmp_path / "t.db")))
    yield str(path)
    M._state.db = None
    tenancy.request_resolver = None


def status(client, fn, **args):
    from pyweb.rpc import RPCError
    try:
        client.rpc(fn, **args)
        return 200
    except RPCError as exc:
        return exc.status


def two_teams(app_file):
    ann, bo = TestClient(app_file), TestClient(app_file)
    ann.login("ann@example.com")
    bo.login("bo@example.com")
    ann_org = ann.rpc("new_org", name="Acme")
    bo_org = bo.rpc("new_org", name="Globex")
    return ann, bo, ann_org, bo_org


def test_rows_stay_inside_their_organization(app_file):
    ann, bo, ann_org, bo_org = two_teams(app_file)
    p1 = ann.rpc("add_project", name="Rocket")
    bo.rpc("add_project", name="Widget")
    assert ann.rpc("projects") == ["Rocket"] and bo.rpc("projects") == ["Widget"]
    assert status(bo, "rename", project_id=p1, name="x") == 404       # not even confirmed to exist
    assert status(bo, "steal", project_id=p1, org_id=bo_org) == 404  # loaded unscoped, still refused
    assert status(bo, "move_all", org_id=ann_org) == 403
    assert ann.rpc("projects") == ["Rocket"]


def test_switching_and_invitations(app_file):
    from pyweb import mail
    ann, bo, ann_org, bo_org = two_teams(app_file)
    ann.rpc("add_project", name="Rocket")
    mail.OUTBOX.clear()
    ann.rpc("invite", email="bo@example.com", role="viewer")
    token = mail.OUTBOX[-1].text.split("/invite/")[1].split()[0]
    assert bo.rpc("accept", token=token) == "viewer"
    assert bo.rpc("projects") == ["Rocket"]                                  # now working in Acme
    assert status(bo, "add_project", name="x") == 403                 # viewers read only
    assert bo.rpc("switch", org_id=bo_org) == "globex"
    assert bo.rpc("projects") == []
    assert status(bo, "switch", org_id=999) == 422
    assert status(bo, "accept", token=token) != 200                         # an invitation works once


def test_jobs_run_for_the_organization_that_queued_them(app_file):
    ann, bo, ann_org, bo_org = two_teams(app_file)
    ann.rpc("add_project", name="A1")
    ann.rpc("add_project", name="A2")
    bo.rpc("add_project", name="B1")
    from pyweb.jobs.core import backend
    ann.rpc("count_later")
    bo.rpc("count_later")
    ran = jobs.Worker(concurrency=1, schedule=False).drain(timeout=10)
    assert ran == 2
    results = sorted(r["result"] for r in backend().list(name="count_projects"))
    assert results == [1, 2]


def test_outside_requests_tenant_models_are_unscoped_unless_scoped(app_file):
    ann, bo, ann_org, bo_org = two_teams(app_file)
    ann.rpc("add_project", name="A1")
    bo.rpc("add_project", name="B1")
    Project = ann.site.app.module.Project
    assert len(Project.query().all()) == 2                      # a script sees everything
    with tenancy.org_scope(ann_org):
        assert [p.name for p in Project.query()] == ["A1"]
        p = Project.create(name="A2")
        assert p.org_id == ann_org
    with tenancy.org_scope(None):
        assert Project.query().all() == []
    with system(), tenancy.org_scope(ann_org):
        assert len(Project.query().all()) == 3                  # system() turns scoping off


def test_tenant_models_get_an_org_column():
    from pyweb.models import Model

    class Thing(Model, tenant=True):
        name: str

    meta = Thing._meta
    assert meta.tenant and "org_id" in {f.column for f in meta.columns()}
    assert meta.field("org_id").readonly


from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

OPS = st.lists(st.tuples(st.sampled_from(["create", "all", "get", "where", "update", "delete", "bulk", "count"]),
                         st.integers(0, 2), st.integers(0, 30), st.text("abc", max_size=3)), max_size=25)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(ops=OPS)
def test_no_sequence_of_operations_crosses_organizations(ops, tmp_path_factory):
    """Random operations by three organizations: each only ever sees and changes its own rows."""
    from pyweb import models as M
    from pyweb.db import SQLiteDB
    from pyweb.models import Field, Model
    from pyweb.rpc import RPCError
    from pyweb.authkit.orgs import Organization

    class Box(Model, tenant=True):
        label: str = Field("", max=20)

    old = M._state.db
    db = SQLiteDB(":memory:")
    M._state.db = None
    M.use_database(db, auto_create=True)
    try:
        orgs = [Organization.create(name=f"Org {i}", slug=f"org-{i}").id for i in range(3)]
        owner = {}
        for op, who, n, label in ops:
            org = orgs[who]
            with tenancy.org_scope(org):
                try:
                    if op == "create":
                        b = Box.create(label=label)
                        owner[b.id] = org
                    elif op == "all":
                        assert all(owner[b.id] == org for b in Box.query().all())
                    elif op == "get":
                        b = Box.get(n)
                        assert b is None or owner[b.id] == org
                    elif op == "where":
                        assert all(owner[b.id] == org for b in Box.where(label=label))
                    elif op == "count":
                        assert Box.query().count() == sum(1 for o in owner.values() if o == org)
                    elif op == "update":
                        with M.system():
                            b = Box.get(n)
                        if b is not None:
                            b.label = "x"
                            try:
                                b.save()
                                assert owner[b.id] == org
                            except RPCError:
                                assert owner[b.id] != org
                    elif op == "delete":
                        Box.where(Box.id == n).delete()
                        with M.system():
                            gone = Box.get(n) is None
                        if n in owner and owner[n] == org:
                            assert gone
                            del owner[n]
                        elif n in owner:
                            assert not gone
                    elif op == "bulk":
                        with pytest.raises(RPCError):
                            Box.where(Box.id > 0).update(org_id=orgs[(who + 1) % 3])
                except RPCError:
                    pass
        with M.system():
            assert {b.id: b.org_id for b in Box.query()} == owner
    finally:
        M._state.db = old
        db.close()
