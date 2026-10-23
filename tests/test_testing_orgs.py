"""Testing apps with organizations: TestClient.login(org=...), as_org(), Factory.for_org(), and
page-level org_role= / feature= guards."""

import pytest

from pyweb import auth as pyauth
from pyweb import models as M
from pyweb import tenancy
from pyweb.testing import Factory, TestClient, as_org

APP = '''
from pyweb import App, Model, server

app = App(database="sqlite:///{db}", title="Teams")
auth = app.use_auth(signup="instant", orgs=True)
auth.plans({{"free": {{"exports": False}}, "pro": {{"exports": True}}}})


class Project(Model, tenant=True):
    name: str


@server(login=True)
def projects() -> list:
    return [p.name for p in Project.query().order("id")]


@server(login=True, org_role=["owner", "admin"])
def add_project(name: str) -> str:
    return Project.create(name=name).name


@app.page("/")
def Home():
    <p>home</p>


@app.page("/settings", org_role=["owner", "admin"])
def Settings():
    <h1>Settings</h1>


@app.page("/exports", feature="exports")
def Exports():
    <h1>Exports</h1>
'''


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(M._state, "db", None)
    monkeypatch.setattr(pyauth, "_versions", None)
    monkeypatch.setitem(pyauth.KIT, "kit", None)
    monkeypatch.setenv("PYWEB_BREACHED_PASSWORDS", "0")
    path = tmp_path / "app.pyweb"
    path.write_text(APP.format(db=tmp_path / "t.db"))
    yield TestClient(str(path))
    M._state.db = None
    tenancy.request_resolver = None


def _project(client):
    return client.app.module.Project


def test_login_with_an_org_scopes_everything_to_it(client):
    from pyweb.rpc import RPCError
    client.login("ann@example.com", org="Acme")
    assert client.rpc("add_project", name="Rocket") == "Rocket"
    client.logout()
    client.login("bo@example.com", org="Globex")
    assert client.rpc("projects") == []                         # another organization's rows don't show
    client.rpc("add_project", name="Widget")
    client.logout()
    client.login("cy@example.com", org="Acme", org_role="member")   # joins the existing Acme
    assert client.rpc("projects") == ["Rocket"]
    with pytest.raises(RPCError) as err:
        client.rpc("add_project", name="Nope")
    assert err.value.status == 403


def test_pages_take_org_role_and_feature(client):
    client.login("ann@example.com", org="Acme")
    assert client.get("/settings").status == 200
    assert client.get("/exports").status == 403               # the free plan has no exports
    from pyweb.authkit.orgs import Organization
    from pyweb.models import system
    with system():
        acme = Organization.where(name="Acme").first()
        acme.plan = "pro"
        acme.save(validate=False)
    assert client.get("/exports").status == 200
    client.logout()
    client.login("cy@example.com", org="Acme", org_role="viewer")
    page = client.get("/settings")
    assert page.status == 403 and "owner or admin role" in page.text
    client.logout()
    assert client.get("/settings").status == 303               # signed out: to /login


def test_as_org_and_factories(client):
    client.get("/")                                           # load the app (and its tables)
    Project = _project(client)
    from pyweb.authkit.orgs import Organization
    from pyweb.models import system
    with system():
        acme = Organization.create(name="Acme", slug="acme")
        globex = Organization.create(name="Globex", slug="globex")
    projects = Factory(Project, name=lambda n: f"Project {n}")
    projects.for_org(acme).create_batch(2)
    projects.for_org(globex).create()
    with as_org(acme):
        assert [p.name for p in Project.query().order("id")] == ["Project 1", "Project 2"]
    with as_org(globex):
        assert [p.name for p in Project.query()] == ["Project 3"]
        assert Project.create(name="Mine").org_id == globex.id
