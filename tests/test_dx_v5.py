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


# ------------------------------------------------------------------- MCP

def test_mcp_data_tools(tmp_path, monkeypatch):
    from pyweb import mcp
    scaffold(str(tmp_path / "team"), template="saas")
    monkeypatch.chdir(tmp_path / "team")
    monkeypatch.setattr(mcp, "APPS", mcp._Apps())
    p = {"path": "app.pyweb"}
    schema = mcp.tool_db_schema(p)
    project = next(m for m in schema["models"] if m["model"] == "Project")
    owner = next(f for f in project["fields"] if f["name"] == "owner_id")
    assert owner["references"] == "User" and owner["readonly"] and project["policy"]
    assert "projects" in schema["tables"] and schema["drift"] == []

    client = mcp.APPS.client("app.pyweb")
    client.login("ann@example.com")
    made = client.rpc("create_project", project={"name": "Docs"})
    got = mcp.tool_db_query({**p, "sql": "SELECT name FROM projects WHERE id = ?", "params": [made["id"]]})
    assert got == {"columns": ["name"], "rows": [["Docs"]], "truncated": False}
    users = mcp.tool_db_query({**p, "sql": "SELECT email, password_hash FROM users"})
    assert users["rows"] == [["ann@example.com", "[redacted]"]]
    assert mcp.tool_db_query({**p, "sql": "SELECT * FROM tasks", "limit": 1})["truncated"] is False
    for bad in ("DELETE FROM users", "SELECT 1; DROP TABLE users", "UPDATE users SET name = 'x'",
                "WITH gone AS (DELETE FROM users RETURNING *) SELECT * FROM gone", "PRAGMA writable_schema = 1"):
        with pytest.raises(ValueError, match="read-only"):
            mcp.tool_db_query({**p, "sql": bad})
    assert mcp.tool_db_query({**p, "sql": "SELECT count(*) FROM users WHERE name = 'drop table'"})["rows"] == [[0]]

    jobs_ = mcp.tool_jobs(p)
    assert jobs_["counts"] == {"queued": 1} and jobs_["jobs"][0]["name"] == "welcome_tasks"
    assert mcp.tool_jobs({**p, "run": True}) == {"ran": 1}
    reqs = mcp.tool_requests({"limit": 5})["requests"]
    assert reqs[-1]["route"] == "rpc:create_project" and reqs[-1]["events"][0]["kind"] == "job"

    status = mcp.tool_migrations(p)
    assert status["migrations"] == [{"label": "0001_initial", "applied": True}]
    app = (tmp_path / "team" / "app.pyweb")
    app.write_text(app.read_text().replace('    done: bool = False\n', '    done: bool = False\n    notes: str = ""\n'))
    diff = mcp.tool_migrations({**p, "action": "diff", "name": "task notes"})
    assert diff["written"] and any("notes" in c for c in diff["changes"])
    assert mcp.tool_migrations({**p, "action": "upgrade"})["applied"] == ["0002_task_notes"]
    names = {t["name"] for t in mcp.TOOLS}
    assert {"pyweb_db_schema", "pyweb_db_query", "pyweb_migrations", "pyweb_jobs", "pyweb_requests"} <= names


# --------------------------------------------------------------- language server

LSP_APP = '''from pyweb import App, Field, Form, Input, Model, Submit, server
from pyweb.authkit import User

app = App(database="sqlite:///app.db")


class Post(Model):
    title: str = Field(max=120)
    author: User = Field(readonly=True)
    tags: list["Tag"] = []


class Tag(Model):
    name: str = ""


@server
def save(post: Post):
    post.save()


@app.page("/")
def Home():
    posts = Post.query().order("-id")
    shown = Post.query().include("author")
    <ul>
        for p in posts:
            <li>{p.title} by {p.author.name}</li>
        for p in shown:
            <li>{p.author.name}</li>
    </ul>
    <Form action={save}>
        <Input name="titel" />
        <Submit>Save</Submit>
    </Form>
'''


def test_lsp_knows_the_data():
    from pyweb import lsp
    from pyweb import lsp_data as D
    tree, ui = D.parse(LSP_APP)
    known = D.models(tree)
    assert known["Post"]["relations"] == {"author": "User"} and known["Post"]["m2m"] == {"tags": "Tag"}
    assert "email" in known["User"]["fields"]                   # the auth kit's User, without running it
    labels = lambda items: [i["label"] for i in items]  # noqa: E731
    assert {"title", "author", "tags", "where", "include"} <= set(labels(D.complete("    x = Post.", tree)))
    assert labels(D.complete('    x = Post.where(title="a").include("', tree)) == ["author", "tags"]
    assert "-title" in labels(D.complete('    x = Post.query().order("-', tree))
    assert "title=" in labels(D.complete("    x = Post.where(ti", tree))
    assert "title__icontains=" in labels(D.complete("    x = Post.where(ti", tree))
    assert "max=" in labels(D.complete("    name: str = Field(m", tree))
    assert labels(D.form_completion('        <Input name="', tree, ui, 999)) == ["tags", "title"]  # no readonly
    found = {d.get("code"): d for d in lsp.diagnostics(LSP_APP, "app.pyweb")}
    assert "has no field 'titel'" in found["unknown-form-field"]["message"]
    n1 = [d for d in lsp.diagnostics(LSP_APP, "app.pyweb") if d.get("code") == "missing-include"]
    assert len(n1) == 1 and "include('author')" in n1[0]["message"]        # the included loop is fine
    assert n1[0]["range"]["start"]["line"] == LSP_APP.splitlines().index(
        "            <li>{p.title} by {p.author.name}</li>")


def test_lsp_migration_lens(tmp_path):
    from pyweb import lsp
    app = tmp_path / "app.pyweb"
    app.write_text(LSP_APP.replace('sqlite:///app.db', f'sqlite:///{tmp_path / "x.db"}'))
    (tmp_path / "migrations").mkdir()
    sent = []
    server = lsp.LanguageServer(sent.append)
    uri = lsp._uri(str(app))
    server.dispatch("textDocument/didOpen", {"textDocument": {"uri": uri, "text": app.read_text()}})
    server.check_migrations(uri, wait=True)
    lens = server.code_lenses(uri)
    assert lens and lens[0]["command"]["command"] == "pyweb.makeMigration"
    assert "needs a migration" in lens[0]["command"]["title"]
    warned = [d for m in sent if m.get("method") == "textDocument/publishDiagnostics"
              for d in m["params"]["diagnostics"] if d.get("code") == "migration-needed"]
    assert warned and "create table posts" in warned[-1]["message"]
    from pyweb.cli import main
    main(["db", "diff", "--app", str(app), "--database", "sqlite:///:memory:"])
    server.check_migrations(uri, wait=True)
    assert "up to date" in server.code_lenses(uri)[0]["command"]["title"]


def test_db_check_for_ci(tmp_path, capsys):
    from pyweb.cli import main
    app = tmp_path / "app.pyweb"
    app.write_text(f'from pyweb import App, Model\napp = App(database="sqlite:///{tmp_path / "c.db"}")\n\n'
                   'class Note(Model):\n    text: str = ""\n')
    with pytest.raises(SystemExit) as e:
        main(["db", "check", "--app", str(app)])
    assert e.value.code == 1 and "create table notes" in capsys.readouterr().out
    main(["db", "diff", "--app", str(app), "--database", "sqlite:///:memory:"])
    main(["db", "check", "--app", str(app)])
    assert "migrations match the Models" in capsys.readouterr().out


# ------------------------------------------------------------------ security pass

def test_csp_allows_only_the_pages_own_inline_styles():
    import base64
    import hashlib
    from pyweb.serve import DEFAULT_CSP, csp_for
    assert "'unsafe-inline'" not in DEFAULT_CSP.split("style-src-attr")[0]
    assert "form-action 'self'" in DEFAULT_CSP and "style-src-attr 'unsafe-inline'" in DEFAULT_CSP
    css = b".card{color:red}"
    html = b"<head><style>" + css + b"</style><style media='print'>p{}</style></head><style>" + css + b"</style>"
    policy = csp_for(DEFAULT_CSP, html)
    digest = base64.b64encode(hashlib.sha256(css).digest()).decode()
    style = policy.split("style-src ")[1].split(";")[0]
    assert style.count(f"'sha256-{digest}'") == 1 and style.count("'sha256-") == 2
    custom = "default-src 'self'; style-src 'self' 'unsafe-inline'"
    assert csp_for(custom, html) == custom          # a policy that allows inline styles stays as it is


CHECK_APP = '''from pyweb import App, Field, Model, server
from pyweb.authkit import User

app = App(database="sqlite:///app.db")
auth = app.use_auth()


class Note(Model):
    text: str = ""
    owner: User = Field(readonly=True)


class Tag(Model):
    name: str = ""


@server
def search(q: str) -> list:
    return db.execute(f"SELECT * FROM notes WHERE text = '{q}'").dicts()


@server
def safe(q: str) -> list:
    return db.execute("SELECT * FROM notes WHERE text = ?", (q,)).dicts()


@app.page("/me")
def Me():
    name = auth.user().name
    <p>{name}</p>


@app.page("/mine", login=True)
def Mine():
    name = auth.user().name
    <p>{name}</p>
'''


def test_check_finds_app_level_risks(tmp_path, capsys):
    from pyweb.cli import main
    from pyweb.security import check_source
    found = {(f["kind"], f["line"]) for f in check_source(CHECK_APP, "app.pyweb")}
    lines = CHECK_APP.splitlines()
    at = lambda text: next(i for i, l in enumerate(lines, 1) if text in l)  # noqa: E731
    assert ("sql-injection", at('f"SELECT')) in found
    assert not any(k == "sql-injection" and line == at('"SELECT * FROM notes WHERE text = ?"') for k, line in found)
    assert ("page-needs-login", at("name = auth.user().name")) in found       # only the page without login=True
    assert sum(1 for k, _ in found if k == "page-needs-login") == 1
    assert ("missing-policy", at("class Note(Model)")) in found
    assert not any(k == "missing-policy" and line == at("class Tag(Model)") for k, line in found)
    fixed = CHECK_APP + "\nNote.policy(read=lambda user: Note.owner_id == (user.id if user else -1))\n"
    assert not any(f["kind"] == "missing-policy" for f in check_source(fixed, "app.pyweb"))
    (tmp_path / "app.pyweb").write_text(CHECK_APP.replace("db.execute", "app.db.execute"))
    main(["check", str(tmp_path / "app.pyweb")])                              # warnings: still exit 0
    with pytest.raises(SystemExit) as e:
        main(["check", "--strict", str(tmp_path / "app.pyweb")])
    assert e.value.code == 1 and "missing-policy" in capsys.readouterr().out


# --------------------------------------------------------------- upgrading

OLD_APP = '''import os
from pyweb import App, server, auth
from pyweb.jobs import task
from pyweb.models import Model, TextField

Model.configure("shop.db")

app = App(title="Old shop")


class Item(Model):
    name = TextField()


@server
def token() -> str:
    return auth.issue_session({"sub": 1}, os.environ["PYWEB_AUTH_SECRET"])


@app.page("/")
def Home():
    items = [i.name for i in Item.all()]
    <ul id="items">
        for name in items:
            <li>{name}</li>
    </ul>
'''


def test_upgrade_a_04_app_keeps_its_data(tmp_path, monkeypatch, capsys):
    import sqlite3
    from pyweb import upgrade as U
    from pyweb.cli import main
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.pyweb").write_text(OLD_APP)
    (tmp_path / "Procfile").write_text("release: pyweb db rollback\nweb: pyweb db migrate && pyweb serve dist\n")
    (tmp_path / "deploy.sh").write_text("pyweb deploy --port 9000 --target k8s --out k8s\n")
    db = sqlite3.connect(tmp_path / "shop.db")                     # rows written by the 0.4 app
    db.execute("CREATE TABLE item (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")  # 0.4 naming
    db.executemany("INSERT INTO item (name) VALUES (?)", [("pen",), ("cup",)])
    db.commit()
    codes = {f.code for f in U.check("app.pyweb")}
    assert codes >= {"model-configure", "memory-jobs", "raw-secret", "db-command", "deploy-target"}
    with pytest.raises(SystemExit):
        main(["upgrade"])
    changed = U.fix("app.pyweb")
    assert set(changed) == {"app.pyweb", "Procfile", "deploy.sh"}
    assert (tmp_path / "Procfile").read_text() == "release: pyweb db downgrade\nweb: pyweb db upgrade && pyweb serve dist\n"
    assert (tmp_path / "deploy.sh").read_text() == "pyweb deploy k8s --port 9000 --out k8s\n"
    source = (tmp_path / "app.pyweb").read_text()
    assert 'App(database="sqlite:///shop.db", title="Old shop")' in source and "configure" not in source
    assert {f.code for f in U.check("app.pyweb")} == {"memory-jobs", "raw-secret"}   # left to a person
    page = TestClient("app.pyweb").get("/")
    assert page.status == 200 and "<li>pen</li>" in page.text and "<li>cup</li>" in page.text


def test_strict_deprecations(monkeypatch):
    from pyweb.deprecation import PyWebDeprecationError, PyWebDeprecationWarning
    with pytest.warns(PyWebDeprecationWarning, match="App\\(database="):
        Model.configure(":memory:")
    monkeypatch.setenv("PYWEB_STRICT_DEPRECATIONS", "1")
    with pytest.raises(PyWebDeprecationError, match="PYWEB_STRICT_DEPRECATIONS"):
        Model.configure(":memory:")
    from pyweb.jobs import Queue
    with pytest.raises(PyWebDeprecationError, match="app.job"):
        Queue()


def test_validation_messages_name_each_field_once():
    from pyweb.rules import ValidationError
    assert str(ValidationError({"body": "Body is required"})) == "Body is required"
    assert str(ValidationError({"title": "is too long"})) == "title is too long"
    assert str(ValidationError("Pick a date")) == "Pick a date"
