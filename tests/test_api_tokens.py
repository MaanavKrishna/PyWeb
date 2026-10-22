"""API tokens: Authorization: Bearer for server functions, scoped, revocable, never for pages."""

import pytest

from pyweb.testing import TestClient
from tests.test_tenancy import APP, app_file  # noqa: F401 - fixture

EXTRA = '''

@server(login=True)
def make_token(name: str, scopes: list[str] = ["*"]) -> str:
    row, token = auth.create_token(auth.user(), name, scopes, org=auth.org())
    return token


@server(login=True)
def whoami() -> str:
    return auth.user().email + "@" + (auth.org().slug if auth.org() else "-")


@server(fresh=60)
def sensitive() -> str:
    return "ok"
'''


@pytest.fixture
def api_app(app_file):  # noqa: F811
    with open(app_file, "a") as fh:
        fh.write(EXTRA)
    return app_file


def call(app, token, fn, **args):
    import json
    c = TestClient(app)
    resp = c.request("POST", f"/__pyweb/rpc/{fn}", json.dumps({"args": args}).encode(),
                     {"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    return resp.status, (resp.json() if resp.body else {})


def test_tokens_act_as_their_user_and_organization(api_app):
    ann = TestClient(api_app)
    ann.login("ann@example.com")
    ann.rpc("new_org", name="Acme")
    ann.rpc("add_project", name="Rocket")
    token = ann.rpc("make_token", name="ci")
    assert token.startswith("pwk_")
    assert call(api_app, token, "whoami") == (200, {"result": "ann@example.com@acme"})
    assert call(api_app, token, "projects") == (200, {"result": ["Rocket"]})
    status, body = call(api_app, token, "sensitive")
    assert status == 401                                      # a token is never a recent sign-in
    assert call(api_app, "pwk_" + "x" * 43, "whoami")[0] == 401
    assert call(api_app, token[:-1] + ("A" if token[-1] != "A" else "B"), "whoami")[0] == 401


def test_scopes_limit_what_a_token_can_call(api_app):
    ann = TestClient(api_app)
    ann.login("ann@example.com")
    ann.rpc("new_org", name="Acme")
    token = ann.rpc("make_token", name="read only", scopes=["projects"])
    assert call(api_app, token, "projects")[0] == 200
    status, body = call(api_app, token, "add_project", name="x")
    assert status == 403 and "can't call add_project" in body["error"]["message"]


def test_tokens_dont_open_pages_and_can_be_revoked(api_app):
    ann = TestClient(api_app)
    user = ann.login("ann@example.com")
    ann.rpc("new_org", name="Acme")
    token = ann.rpc("make_token", name="ci")
    page = TestClient(api_app).request("GET", "/account", b"", {"Authorization": f"Bearer {token}"})
    assert page.status == 303                                 # not signed in as far as pages go
    kit = ann.site.app.app.auth
    from pyweb.authkit import ApiToken
    from pyweb.models import system
    with system():
        row = ApiToken.where(user_id=user.id).first()
        assert row.token_hash != token and token not in str(row.to_dict())
    assert kit.revoke_token(user, row.id)
    assert call(api_app, token, "whoami")[0] == 401


def test_the_tokens_page(tmp_path, monkeypatch):
    import re
    from tests.test_org_pages import APP as ORG_APP, person
    from pyweb import auth as pyauth, models as M, tenancy
    from pyweb.hosting import Site
    monkeypatch.setattr(M._state, "db", None)
    monkeypatch.setattr(pyauth, "_versions", None)
    monkeypatch.setenv("PYWEB_BREACHED_PASSWORDS", "0")
    app = tmp_path / "app.pyweb"
    app.write_text(ORG_APP.replace("{db}", str(tmp_path / "t.db")))
    try:
        site = Site(str(app), debug=True, rate_limit=False)
        ann = person(site, "ann@example.com", "Ann")
        _, _, html = ann.post("/account/tokens", {"action": "create", "name": "deploy bot", "scopes": "",
                                                  "days": "30"})
        token = re.search(r'value="(pwk_[^"]+)"', html).group(1)
        _, _, html = ann.get("/account/tokens")
        assert "deploy bot" in html and token not in html and token[:10] in html
        tid = re.search(r'name="id" value="(\d+)"', html).group(1)
        _, _, html = ann.post("/account/tokens", {"action": "revoke", "id": tid})
        assert "Revoked" in html
    finally:
        M._state.db = None
        tenancy.request_resolver = None
