"""Editor support for server options, organizations and webhooks (pyweb/lsp_teams.py)."""

from pyweb import lsp

APP = '''from pyweb import App, Model, server
from pyweb.models import system

app = App(database="sqlite:///app.db")
auth = app.use_auth(orgs=True)
auth.plans({"free": {"exports": False}, "pro": {"exports": True, "seats": 50}})


class Project(Model, tenant=True):
    name: str


class Note(Model):
    text: str


@server(rate="10/fortnight", org_role="boss")
def everything() -> list:
    return [p.name for p in Project.query().unscoped()]


@server(rate="5/minute", per="org", org_role=["owner", "admin"])
def fine() -> int:
    with system():
        n = Project.query().unscoped().count()
    return n + Note.query().unscoped().count()


@app.page("/")
def Home():
    <p>home</p>
'''


def _labels(prefix, text=APP):
    lines = text.splitlines()
    lines.append(prefix)
    found = lsp.completions("\n".join(lines), "app.pyweb", len(lines) - 1, len(prefix))
    return [i["label"] for i in found]


def test_completions():
    assert {"rate", "per", "org_role", "feature", "login"} <= set(_labels("@server("))
    assert "rate" not in _labels('@server(rate="5/minute", ')
    assert {"title", "org_role", "feature", "noindex"} <= set(_labels('@app.page("/x", '))
    assert _labels('@server(org_role="') == ["owner", "admin", "member", "viewer"]
    assert _labels('@server(org_role=["owner", "') == ["owner", "admin", "member", "viewer"]
    assert _labels('@server(per="') == ["user", "ip", "org", "all"]
    assert "10/minute" in _labels('@server(rate="')
    assert _labels('@server(feature="') == ["exports"]                # from auth.plans(...): not "seats"
    assert _labels('@app.webhook("/hooks/gh", secret=s, scheme="') == ["standard", "github", "stripe"]
    assert _labels("class Invoice(Model, ") == ["tenant=True"]


def test_problems():
    found = {(d["code"], d["range"]["start"]["line"] + 1) for d in lsp.diagnostics(APP, "app.pyweb")}
    line = APP.splitlines().index('@server(rate="10/fortnight", org_role="boss")') + 1
    assert ("bad-rate", line) in found and ("unknown-org-role", line) in found
    unscoped = [d for d in lsp.diagnostics(APP, "app.pyweb") if d["code"] == "tenant-unscoped"]
    assert len(unscoped) == 1                     # inside `with system():` and on a plain Model: fine
    assert "every organization's rows" in unscoped[0]["message"]
    assert unscoped[0]["range"]["start"]["line"] + 1 == APP.splitlines().index(
        "    return [p.name for p in Project.query().unscoped()]") + 1
