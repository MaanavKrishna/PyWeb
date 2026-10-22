"""The organization pages: create, invite, accept, change roles, transfer, remove and leave, all without JavaScript."""

import re
import urllib.parse

import pytest

from pyweb import auth as pyauth
from pyweb import mail, tenancy
from pyweb import models as M
from pyweb.hosting import Site
from tests.test_authkit import PASSWORD, Browser

APP = '''
from pyweb import App, Model
app = App(database="sqlite:///{db}", title="Teams")
auth = app.use_auth(signup="instant", orgs=True)


class Doc(Model, tenant=True):
    title: str


@app.page("/")
def Home():
    <p>home</p>
'''


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(M._state, "db", None)
    monkeypatch.setattr(pyauth, "_versions", None)
    monkeypatch.setitem(pyauth.KIT, "kit", None)
    monkeypatch.setenv("PYWEB_BREACHED_PASSWORDS", "0")
    mail.OUTBOX.clear()
    app = tmp_path / "app.pyweb"
    app.write_text(APP.replace("{db}", str(tmp_path / "t.db")))
    yield Site(str(app), debug=True, rate_limit=False)
    M._state.db = None
    tenancy.request_resolver = None


def person(site, email, name):
    b = Browser(site)
    b.get("/signup")
    status, _, html = b.post("/signup", {"email": email, "name": name, "password": PASSWORD})
    assert status == 303, html[:300]
    return b


def test_the_whole_team_flow(site):
    ann = person(site, "ann@example.com", "Ann")
    bo = person(site, "bo@example.com", "Bo")
    status, headers, _ = ann.post("/orgs", {"action": "create", "name": "Acme Rockets"})
    assert status == 303 and headers["Location"] == "/orgs/acme-rockets"
    _, _, html = ann.get("/orgs/acme-rockets")
    assert "Your role: owner" in html and "Invite someone" in html

    # Bo isn't a member: the organization doesn't exist for him.
    assert bo.get("/orgs/acme-rockets")[0] == 404
    assert bo.get("/orgs/no-such-org")[0] == 404

    _, _, html = ann.post("/orgs/acme-rockets", {"action": "invite", "email": "BO@example.com", "role": "member"})
    assert "Invitation sent to bo@example.com" in html and "Waiting for an answer" in html
    link = urllib.parse.urlparse(re.search(r"(http://\S+/invite/\S+)", mail.OUTBOX[-1].text).group(1)).path

    signed_out = Browser(site)
    status, headers, _ = signed_out.get(link)
    assert status == 303 and headers["Location"].startswith("/login?next=%2Finvite%2F")
    status, headers, _ = bo.post(link, {})
    assert status == 303
    _, _, html = bo.get("/orgs/acme-rockets")
    assert "Your role: member" in html and "Invite someone" not in html
    assert bo.post(link, {})[0] == 404                       # used once

    bo_id = re.search(r'name="user" value="(\d+)"', ann.get("/orgs/acme-rockets")[2]).group(1)
    _, _, html = ann.post("/orgs/acme-rockets", {"action": "role", "user": bo_id, "role": "admin"})
    assert "Role changed" in html
    _, _, html = bo.get("/orgs/acme-rockets")
    assert "Your role: admin" in html

    # The only owner can't leave; ownership can be handed over, then they can.
    _, _, html = ann.post("/orgs/acme-rockets", {"action": "leave"})
    assert "last owner" in html
    _, _, html = ann.post("/orgs/acme-rockets", {"action": "transfer", "user": bo_id})
    assert "is now an owner" in html
    status, headers, _ = ann.post("/orgs/acme-rockets", {"action": "leave"})
    assert status == 303 and headers["Location"] == "/orgs"
    assert ann.get("/orgs/acme-rockets")[0] == 404


def test_admins_cant_touch_owners_and_members_cant_manage(site):
    ann = person(site, "ann@example.com", "Ann")
    bo = person(site, "bo@example.com", "Bo")
    cy = person(site, "cy@example.com", "Cy")
    ann.post("/orgs", {"action": "create", "name": "Acme"})
    for who, role in (("bo@example.com", "admin"), ("cy@example.com", "member")):
        ann.post("/orgs/acme", {"action": "invite", "email": who, "role": role})
        link = urllib.parse.urlparse(re.search(r"(http://\S+/invite/\S+)", mail.OUTBOX[-1].text).group(1)).path
        (bo if who.startswith("bo") else cy).post(link, {})
    ids = {}
    for row in re.findall(r"<tr>(.*?)</tr>", ann.get("/orgs/acme")[2], re.S):
        email, uid = re.search(r"<small>([^<]+)</small>", row), re.search(r'name="user" value="(\d+)"', row)
        if email and uid:
            ids[email.group(1)] = uid.group(1)
    from pyweb.authkit import User
    from pyweb.models import system
    with system():
        ids["ann@example.com"] = str(User.where(email="ann@example.com").first().id)
    _, _, html = bo.post("/orgs/acme", {"action": "remove", "user": ids["ann@example.com"]})
    assert "Only owners can remove an owner" in html
    _, _, html = bo.post("/orgs/acme", {"action": "role", "user": ids["cy@example.com"], "role": "owner"})
    assert "Only owners" in html
    _, _, html = cy.post("/orgs/acme", {"action": "invite", "email": "x@example.com"})
    assert "only owners and admins" in html


def test_an_invitation_is_for_its_email_only(site):
    ann = person(site, "ann@example.com", "Ann")
    eve = person(site, "eve@example.com", "Eve")
    ann.post("/orgs", {"action": "create", "name": "Acme"})
    ann.post("/orgs/acme", {"action": "invite", "email": "bo@example.com"})
    link = urllib.parse.urlparse(re.search(r"(http://\S+/invite/\S+)", mail.OUTBOX[-1].text).group(1)).path
    status, _, html = eve.post(link, {})
    assert status == 422 and "for bo@example.com" in html
    assert eve.get("/orgs/acme")[0] == 404


def test_platform_admins_see_every_organization_and_can_filter(site):
    from pyweb.authkit import User
    from pyweb.authkit.orgs import Organization
    from pyweb.models import system
    ann = person(site, "ann@example.com", "Ann")
    ann.post("/orgs", {"action": "create", "name": "Acme"})
    ann.post("/orgs", {"action": "create", "name": "Globex"})
    Doc = site.app.module.Doc
    with system():
        u = User.where(email="ann@example.com").first()
        u.roles = ["admin"]
        u.save(validate=False)
        acme, globex = (Organization.where(slug=s).first().id for s in ("acme", "globex"))
    with tenancy.org_scope(acme):
        Doc.create(title="acme-doc")
    with tenancy.org_scope(globex):
        Doc.create(title="globex-doc")
    _, _, html = ann.get("/admin/docs")
    assert "All organizations" in html and "acme-doc" in html and "globex-doc" in html
    _, _, html = ann.get(f"/admin/docs?org={acme}")
    assert "acme-doc" in html and "globex-doc" not in html
