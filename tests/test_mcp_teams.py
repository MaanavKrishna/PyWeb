"""MCP tools for apps with organizations: signing in (as_user, org) for render/call, pyweb_openapi and
pyweb_security_check."""

import pytest

from pyweb import auth as pyauth
from pyweb import mcp
from pyweb import models as M
from pyweb import tenancy
from tests.test_testing_orgs import APP


@pytest.fixture
def app_path(tmp_path, monkeypatch):
    monkeypatch.setattr(M._state, "db", None)
    monkeypatch.setattr(pyauth, "_versions", None)
    monkeypatch.setitem(pyauth.KIT, "kit", None)
    monkeypatch.setenv("PYWEB_BREACHED_PASSWORDS", "0")
    monkeypatch.setattr(mcp, "APPS", mcp._Apps())
    path = tmp_path / "app.pyweb"
    path.write_text(APP.format(db=tmp_path / "t.db") + '''

@server(login=True)
def everything() -> int:
    return Project.query().unscoped().count()
''')
    yield str(path)
    M._state.db = None
    tenancy.request_resolver = None


def test_render_and_call_as_a_member_of_an_org(app_path):
    added = mcp.tool_call({"path": app_path, "function": "add_project", "args": {"name": "Rocket"},
                           "as_user": "ann@example.com", "org": "Acme"})
    assert added == {"ok": True, "result": "Rocket", "cookies": added["cookies"]}
    other = mcp.tool_call({"path": app_path, "function": "projects", "as_user": "bo@example.com", "org": "Globex"})
    assert other["result"] == []
    viewer = mcp.tool_render({"path": app_path, "url": "/settings", "as_user": "cy@example.com", "org": "Acme",
                              "org_role": "viewer"})
    assert viewer["status"] == 403
    assert mcp.tool_render({"path": app_path, "url": "/settings", "as_user": "ann@example.com",
                            "org": "Acme"})["status"] == 200
    with pytest.raises(ValueError, match="as_user"):
        mcp.tool_render({"path": app_path, "url": "/", "org": "Acme"})


def test_openapi_and_security_check(app_path):
    doc = mcp.tool_openapi({"path": app_path})
    assert doc["openapi"].startswith("3.1") and "/__pyweb/rpc/add_project" in doc["paths"]
    report = mcp.tool_security_check({"path": app_path})
    kinds = {f["kind"] for f in report["findings"]}
    assert "tenant-unscoped" in kinds and report["ok"] is False
    names = {t["name"] for t in mcp.TOOLS}
    assert {"pyweb_openapi", "pyweb_security_check"} <= names
