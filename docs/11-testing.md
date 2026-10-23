# Testing

Apps created with `pyweb new` include a `test_app.py` that checks the
home page renders; run `pytest` in the app folder and add tests next to it.

## `TestClient`: fast, in-process, no browser

`pyweb.testing.TestClient` loads your app exactly like the production
server and lets you request pages and call server functions. Cookies
persist between calls, so sessions work.

```python
from pyweb.testing import TestClient
from pyweb import RPCError
import pytest


def test_notes():
    client = TestClient("app.pyweb")

    assert client.get("/").status == 303            # redirects to /login

    assert client.rpc("sign_in", name="ada") is True
    page = client.get("/")
    assert page.status == 200
    assert "Notes for ada" in page.text

    with pytest.raises(RPCError) as e:
        client.rpc("add_note", body="")
    assert e.value.code == "validation_error"
```

| API | |
|---|---|
| `TestClient(path)` / `TestClient(source="...")` | load an app file or source text |
| `.get(path)`, `.post(path, data)`, `.request(method, path, body, headers)` | return a response with `.status`, `.text`, `.json()`, `.header(name)` |
| `.rpc(name, **args)` | call a server function; returns the result or raises `RPCError` |
| `.login(email, roles=[...])` | sign in as someone without the sign-in form (with `app.use_auth()`, the account is created if needed); returns the user |
| `.login(email, org="Acme", org_role="admin")` | also make them a member of that organization (created if needed) and switch to it |
| `.logout()` | forget the session |
| `.cookies` | the cookie jar |
| `.app.module` | the executed app module, for direct access to its objects |

## Apps with accounts, data and jobs

For an app with a database, give each test its own: change into
pytest's `tmp_path` so `sqlite:///app.db` lands there. Tables are
created as Models are first used (or by your migrations). Set
`PYWEB_WORKER=0` so no background worker runs, and run queued jobs
yourself with `jobs.Worker(schedule=False).drain()`, which returns how
many ran. This file tests the feedback app from the
[README](https://github.com/MaanavKrishna/PyWeb#a-whole-product-in-one-file):

```python
from pathlib import Path

import pytest

from pyweb import RPCError, jobs, mail
from pyweb.testing import Factory, TestClient

APP = str(Path(__file__).parent / "app.pyweb")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                 # each test gets its own SQLite file
    monkeypatch.setenv("PYWEB_WORKER", "0")     # the test runs jobs itself
    return TestClient(APP)


def test_sharing_an_idea(client):
    assert client.get("/").status == 303        # signed out: to /login
    ann = client.login("ann@example.com")       # an account, signed in
    client.rpc("share", idea={"text": "Dark mode"})
    assert jobs.Worker(schedule=False).drain() == 1     # the thank() job ran
    assert "Dark mode" in client.get("/").text

    with pytest.raises(RPCError) as err:
        client.rpc("share", idea={"text": "x"})
    assert err.value.code == "validation_error"
    assert ann.email == "ann@example.com"


def test_ideas_from_a_factory(client):
    from pyweb.authkit import User
    Idea = client.app.module.Idea
    author = User.create(email="bob@example.com", name="Bob")
    ideas = Factory(Idea, text=lambda n: f"Idea {n}", author=author)
    ideas.create_batch(3)
    client.login("ann@example.com")
    page = client.get("/").text
    assert "Idea 1" in page and "Idea 3" in page


def test_emails_land_in_the_outbox(client):
    mail.OUTBOX.clear()
    mail.send("ann@example.com", "Welcome", "Hello!")
    jobs.Worker(schedule=False).drain()          # email goes through the job queue
    assert [m.subject for m in mail.OUTBOX] == ["Welcome"]
```

- `client.login(...)` signs in without the sign-in form, so tests don't
  depend on passwords or emails. Pass `roles=["admin"]` for admin pages.
- `Factory(Model, **defaults)` makes rows: a default can be a value, a
  function of the row number, or another `Factory` for a related row.
  `.build()` makes an unsaved row, `.create()` a saved one,
  `.create_batch(n)` several.
- Emails sent while testing are kept in `pyweb.mail.OUTBOX` (the last
  50) instead of being sent.
- Row policies apply to requests (`client.get`, `client.rpc`), not to
  code the test runs directly, so a test can set up anyone's rows.

## Apps with teams

With `app.use_auth(orgs=True)`, `client.login(..., org=...)` puts the
user in an organization and makes it their current one, so `tenant=True`
Models and `org_role=` guards behave as they would for a real member.
`as_org(org)` runs test code for an organization, and
`Factory(...).for_org(org)` makes tenant rows in one:

```python
from pyweb.authkit.orgs import Organization
from pyweb.testing import Factory, as_org


def test_members_see_their_teams_projects(client):
    Project = client.app.module.Project
    ann = client.login("ann@example.com", org="Rocket team")      # owner
    rocket = Organization.where(name="Rocket team").first()
    Factory(Project, name=lambda n: f"Project {n}", owner=ann).for_org(rocket).create_batch(2)
    client.logout()
    client.login("bob@example.com", org="Rocket team", org_role="member")
    assert "Project 2" in client.get("/projects").text
    client.logout()
    client.login("eve@example.com", org="Other team")
    assert "Project 2" not in client.get("/projects").text      # another team's rows
    with as_org(rocket):
        assert Project.query().count() == 2
```

`org_role` defaults to `"owner"`. Pages and server functions guarded with
`org_role=` or `feature=` answer 403 for the wrong role or plan, which a
test can check with `client.get(...).status` or `pytest.raises(RPCError)`.

## Migrations in CI

`pyweb db check` exits non-zero when a Model changed without a migration,
or when a migration doesn't match the Models. Run it next to `pytest`:

```bash
pyweb check app.pyweb --strict    # compile + security checks; warnings fail too
pyweb db check                    # Models and migrations agree
pytest
```

## Real browser tests

`pyweb.testing.serve(path)` runs the app on a free port. Combine it with
[Playwright](https://playwright.dev/python/) to test interactivity:

```python
from playwright.sync_api import expect, sync_playwright
from pyweb.testing import serve


def test_counter_in_a_browser():
    with serve("app.pyweb") as url, sync_playwright() as p:
        page = p.chromium.launch().new_page()
        page.goto(url)
        page.wait_for_selector("[data-pw-ready]")   # set once the page is interactive
        page.click("text=Count: 0")
        expect(page.locator("button")).to_have_text("Count: 1")
```

`serve()` applies the production Content-Security-Policy, so your
tests also prove the app works under it.

To check accessibility in the browser too, inject
[axe-core](https://github.com/dequelabs/axe-core) with
`context.add_init_script(...)` and fail on serious violations, as PyWeb's
own tests do (`tests/e2e/test_a11y_browser.py`). With an AI assistant,
the `pyweb_review` MCP tool does this for a page in light and dark mode
and at phone width.

## Compile-time checks

`pyweb check app.pyweb` compiles the app and runs the security and
accessibility checks (images without `alt`, clicks a keyboard can't
reach, unlabelled fields, ...); it exits non-zero on errors (and, with
`--strict`, on warnings), which makes it a useful first CI step.
`compile_source(text)` from `pyweb.compiler` gives you the compiled
pages (HTML, JS, signals, placement) if you want to assert on them.
