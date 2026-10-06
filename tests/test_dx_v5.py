"""Developer experience: the saas template end to end, parameter rules, relations declared with
Field(...), hidden and label-less form inputs, TestClient.login and seeds that see the app's Models."""

import os
import re
import subprocess
import sys

import pytest

from pyweb import RPCError
from pyweb import models as M
from pyweb.mcp import TEMPLATES, scaffold
from pyweb.models import Field, Model
from pyweb.testing import TestClient


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    from pyweb import auth as pyauth
    from pyweb import jobs
    jobs.stop_all(0)
    monkeypatch.setattr(M._state, "db", None)
    monkeypatch.setattr(pyauth, "_versions", None)
    monkeypatch.setitem(pyauth.KIT, "kit", None)
    monkeypatch.setenv("PYWEB_BREACHED_PASSWORDS", "0")
    monkeypatch.setenv("PYWEB_WORKER", "0")
    monkeypatch.delenv("PYWEB_ENV", raising=False)
    jobs.use_backend(None)
    yield
    jobs.use_backend(None)
    M._state.db = None


# ------------------------------------------------------------- templates

def test_saas_template_scaffolds_a_working_app(tmp_path):
    assert "saas" in TEMPLATES
    app = tmp_path / "team"
    written = [os.path.relpath(p, app) for p in scaffold(str(app), template="saas", title="Team")]
    for f in ("app.pyweb", "test_app.py", "seeds.py", "README.md", "AGENTS.md", "static/app.css",
              os.path.join("migrations", "0001_initial.py")):
        assert f in written, f
    assert not (app / "app.db").exists()                       # migrations were made elsewhere
    migration = (app / "migrations" / "0001_initial.py").read_text()
    for table in ("users", "projects", "tasks"):
        assert f"create table {table}" in migration
    assert 'App(title="Team"' in (app / "app.pyweb").read_text()
    env = {**os.environ, "PYWEB_WORKER": "0"}
    env.pop("PYWEB_ENV", None)
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_app.py"],
                          cwd=app, env=env, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]
    db = f"sqlite:///{tmp_path / 'team.db'}"
    for cmd in (["db", "upgrade"], ["db", "seed"], ["db", "seed"], ["db", "diff"]):
        out = subprocess.run([sys.executable, "-m", "pyweb.cli", *cmd, "--database", db], cwd=app, env=env,
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
    assert "no changes" in out.stdout                          # the migration matches the Models


def test_every_template_compiles(tmp_path):
    from pyweb.compiler import compile_source
    from pyweb.mcp import _template_source
    for name in TEMPLATES:
        compile_source(_template_source(name), filename=f"{name}.pyweb")


# ------------------------------------------------------- parameter rules

RULES_APP = '''from typing import Annotated
from pyweb import App, Field, Form, Input, Submit, server

app = App()

@server
def short(name: Annotated[str, Field(max=3)]) -> str:
    return name

@server
def titled(n: int, title: str = Field(min=2, max=5), note: str = Field(default="x", max=3)) -> str:
    return f"{n}:{title}:{note}"

@app.page("/")
def Home():
    <Form action={titled} values={{"n": 7}}>
        <Input name="n" type="hidden" />
        <Input name="title" label="" placeholder="Title" />
        <Submit>Go</Submit>
    </Form>
'''


def test_annotated_rules_are_enforced_on_every_call():
    c = TestClient(source=RULES_APP)
    assert c.rpc("short", name="abc") == "abc"
    with pytest.raises(RPCError) as err:
        c.rpc("short", name="toolong")
    assert err.value.code == "validation_error"
    assert err.value.details == {"errors": {"name": "Name must be at most 3 characters"}}


def test_a_field_as_default_is_a_rule_and_a_default():
    c = TestClient(source=RULES_APP)
    assert c.rpc("titled", n=1, title="hey") == "1:hey:x"
    with pytest.raises(RPCError) as err:
        c.rpc("titled", n=1)
    assert err.value.details == {"errors": {"title": "Title is required"}}
    with pytest.raises(RPCError) as err:
        c.rpc("titled", n=1, title="h", note="long")
    assert set(err.value.details["errors"]) == {"title", "note"}
    fn = c.app.module.titled
    assert fn(1, "hey") == "1:hey:x"                           # still callable from Python


def test_hidden_and_label_less_inputs():
    html = TestClient(source=RULES_APP).get("/").text
    hidden = re.search(r'<input type="hidden" name="n"[^>]*>', html)
    assert hidden and 'value="7"' in hidden.group(0)
    assert 'for="pw-titled-n"' not in html                    # no label for the hidden value
    title = re.search(r'<input[^>]*name="title"[^>]*>', html).group(0)
    assert 'aria-label="Title"' in title and 'minlength="2"' in title and 'maxlength="5"' in title
    assert 'for="pw-titled-title"' not in html


# ------------------------------------------------------------- relations

def test_a_relation_declared_with_field_is_a_foreign_key():
    class Owner(Model):
        name: str = ""

    class Thing(Model):
        owner: Owner = Field(readonly=True)
        other: Owner | None = Field(label="Backup owner", default=None)

    fields = Thing._meta.fields
    assert "owner_id" in fields and fields["owner_id"].readonly and not fields["owner_id"].nullable
    assert fields["other_id"].nullable and fields["other_id"].label == "Backup owner"
    from pyweb.models import ModelMeta
    try:
        with pytest.raises(TypeError, match="default row"):
            class Bad(Model):
                owner: Owner = Field(default=1)
            Bad._meta.fields  # noqa: B018 - resolving the fields raises
    finally:                                 # don't leave a broken Model for other tests to trip on
        for reg in (ModelMeta._registry, M._pending):
            reg[:] = [m for m in reg if m.__name__ != "Bad"]
        M._by_name.pop("Bad", None)


# ------------------------------------------------------- TestClient.login

KIT_APP = '''from pyweb import App, server
app = App(database="sqlite:///{db}")
auth = app.use_auth()

@server(login=True, roles=["admin"])
def secret() -> str:
    return "for " + auth.user().email

@app.page("/me", login=True)
def Me():
    <p id="me">{{auth.user().email}}</p>
'''


def test_login_with_the_auth_kit(tmp_path):
    c = TestClient(source=KIT_APP.format(db=tmp_path / "a.db"))
    assert c.get("/me").status == 303
    user = c.login("Ann@Example.com")
    assert user.email == "ann@example.com"
    assert 'id="me">ann@example.com' in c.get("/me").text
    with pytest.raises(RPCError) as err:
        c.rpc("secret")
    assert err.value.code == "forbidden"
    c.login("ann@example.com", roles=["admin"])
    assert c.rpc("secret") == "for ann@example.com"
    c.logout()
    assert c.get("/me").status == 303


def test_login_without_the_kit():
    c = TestClient(source='from pyweb import App, server, session\napp = App()\n\n'
                          '@server(login=True)\ndef who() -> dict:\n    return session.user()\n')
    c.login("bob@example.com", roles=["editor"], name="Bob")
    me = c.rpc("who")
    assert me["sub"] == "bob@example.com" and me["roles"] == ["editor"] and me["name"] == "Bob"


# ----------------------------------------------------------------- seeds

def test_seeds_see_the_apps_models(tmp_path, capsys):
    from pyweb.cli import main
    (tmp_path / "app.pyweb").write_text(
        f'from pyweb import App, Model\napp = App(database="sqlite:///{tmp_path / "s.db"}")\n\n'
        'class Fruit(Model):\n    name: str = ""\n')
    (tmp_path / "seeds.py").write_text(
        "from pyweb.db.seeds import seed\nfrom pyweb.models import model\n\n"
        "@seed\ndef fruit():\n    Fruit.get_or_create(name='apple')  # noqa: F821\n"
        "    model('Fruit').get_or_create(name='pear')\n")
    main(["db", "seed", "--app", str(tmp_path / "app.pyweb")])
    assert "seeded: fruit" in capsys.readouterr().out
    import sqlite3
    assert sqlite3.connect(tmp_path / "s.db").execute("SELECT COUNT(*) FROM fruits").fetchone()[0] == 2
    with pytest.raises(LookupError, match="no Model named 'Nope'"):
        M.model("Nope")
