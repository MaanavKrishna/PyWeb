"""axe-core on the examples, the auth kit's pages and /admin: no serious or critical violations.

axe-core comes from the npm registry (checksum verified, cached in tests/e2e/.cache); without
network access and without a cached copy, these tests skip.
"""

import shutil
import textwrap
from pathlib import Path

import pytest

from pyweb.testing import serve
from tests.e2e.conftest import ready, settle, until

ROOT = Path(__file__).parent.parent.parent
EXAMPLES = ROOT / "examples"


@pytest.fixture(scope="module")
def axe():
    from tests.e2e.fetch_assets import axe_source
    try:
        return axe_source()
    except Exception as exc:  # noqa: BLE001 - offline
        pytest.skip(f"axe-core unavailable: {exc}")


@pytest.fixture
def audit(browser, axe):
    """``audit(page)`` -> serious and critical violations on the page as it is now."""
    contexts = []

    def page(**kw):
        ctx = browser.new_context(**kw)
        # An init script runs before the page's own and isn't subject to its CSP (WebKit applies
        # Trusted Types even to add_script_tag with bypass_csp).
        ctx.add_init_script(script=axe)
        contexts.append(ctx)
        return ctx.new_page()

    def run(pg):
        # Colours mid-animation (a dialog fading in) aren't the colours people read. Two frames
        # first: a transition a focus change starts only shows up in getAnimations() a frame later.
        settled = ("document.getAnimations().every((a) => a.playState !== 'running' || "
                   "a.effect.getComputedTiming().iterations === Infinity)")
        for _ in range(2):
            pg.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
            until(pg, settled)
        found = pg.evaluate("""async () => {
          const r = await axe.run(document, {resultTypes: ["violations"]});
          return r.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
            .map((v) => ({id: v.id, impact: v.impact, help: v.help,
                          nodes: v.nodes.slice(0, 5).map((n) => n.target.join(" ") + "  " + n.failureSummary)}));
        }""")
        return [f"{v['impact']} {v['id']}: {v['help']}\n    " + "\n    ".join(v["nodes"]) for v in found]
    run.page = page
    yield run
    for ctx in contexts:
        ctx.close()


def copy_example(name, tmp_path, monkeypatch):
    shutil.copytree(EXAMPLES / name, tmp_path / name, ignore=shutil.ignore_patterns("*.db", "__pycache__"))
    monkeypatch.chdir(tmp_path / name)
    return str(tmp_path / name / "app.pyweb")


CLIENT_PAGES = [("counter", ["/"]), ("todo", ["/"]), ("showcase", ["/"]), ("site", ["/", "/plants", "/about"]),
                ("chat", ["/", "/chat/general"]), ("auth", ["/", "/register"]), ("ai-chat", ["/"]),
                ("blog", ["/"]), ("dashboard", ["/"])]


@pytest.mark.parametrize("name,paths", CLIENT_PAGES, ids=[n for n, _ in CLIENT_PAGES])
def test_examples(audit, tmp_path, monkeypatch, name, paths):
    app = copy_example(name, tmp_path, monkeypatch)
    problems = []
    with serve(app) as url:
        for scheme in ("light", "dark"):
            pg = audit.page(color_scheme=scheme)
            for path in paths:
                pg.goto(url.rstrip("/") + path)
                ready(pg)
                if name == "todo":
                    for title in ("one", "two"):
                        pg.fill("#new", title)
                        pg.press("#new", "Enter")
                    pg.locator("#list li input[type=checkbox]").first.check()
                settle(pg)
                problems += [f"{path} ({scheme}): {p}" for p in audit(pg)]
    assert not problems, "\n".join(problems)


def test_ui_example_tabs_and_overlays(audit):
    problems = []
    with serve(str(EXAMPLES / "ui" / "app.pyweb")) as url:
        for scheme in ("light", "dark"):
            pg = audit.page(color_scheme=scheme)
            pg.goto(url)
            ready(pg)
            settle(pg)
            for tab in ("Components", "Motion", "Data"):
                pg.get_by_role("tab", name=tab).click()
                settle(pg)
                problems += [f"{tab} ({scheme}): {p}" for p in audit(pg)]
            pg.get_by_role("tab", name="Components").click()
            pg.click("#open-dialog")
            pg.get_by_role("dialog", name="Edit profile").wait_for()
            problems += [f"dialog ({scheme}): {p}" for p in audit(pg)]
            pg.keyboard.press("Escape")
            pg.get_by_role("button", name="Menu").click()
            pg.get_by_role("menu").wait_for()
            problems += [f"menu ({scheme}): {p}" for p in audit(pg)]
            pg.keyboard.press("Escape")
    assert not problems, "\n".join(problems)


ORG_APP = textwrap.dedent('''
    from pyweb import App, Model

    app = App(title="Audit", database="sqlite:///audit.db")
    auth = app.use_auth(signup="instant", orgs=True)


    class Note(Model):
        text: str


    @app.page("/")
    def Home():
        <main><h1>Home</h1></main>
''')


def test_auth_kit_and_admin(audit, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYWEB_BREACHED_PASSWORDS", "0")
    (tmp_path / "app.pyweb").write_text(ORG_APP)
    problems = []
    with serve(str(tmp_path / "app.pyweb")) as url:
        base = url.rstrip("/")
        from pyweb.authkit import User
        from pyweb.authkit.passwords import hash_password
        from pyweb.models import system
        with system():
            User(email="admin@example.com", name="Admin", roles=["admin"], email_verified=True,
                 password_hash=hash_password("correct horse battery")).save()
        for scheme in ("light", "dark"):
            pg = audit.page(color_scheme=scheme)
            for path in ("/login", "/signup", "/reset"):
                pg.goto(base + path)
                problems += [f"{path} ({scheme}): {p}" for p in audit(pg)]
            pg.goto(base + "/login")
            pg.fill("input[name=email]", "wrong@example.com")
            pg.fill("input[name=password]", "not the password")
            pg.click("button[type=submit]")
            problems += [f"/login error ({scheme}): {p}" for p in audit(pg)]
            pg.fill("input[name=email]", "admin@example.com")
            pg.fill("input[name=password]", "correct horse battery")
            pg.click("button[type=submit]")
            pg.wait_for_url(base + "/")
            for path in ("/account", "/account/tokens", "/orgs", "/admin", "/admin/notes", "/admin/notes/new"):
                pg.goto(base + path)
                problems += [f"{path} ({scheme}): {p}" for p in audit(pg)]
    assert not problems, "\n".join(problems)


def test_saas_signed_in(audit, tmp_path, monkeypatch):
    app = copy_example("saas", tmp_path, monkeypatch)
    monkeypatch.setenv("PYWEB_BREACHED_PASSWORDS", "0")
    problems = []
    with serve(app) as url:
        base = url.rstrip("/")
        from pyweb.authkit import User
        from pyweb.authkit.passwords import hash_password
        from pyweb.models import system
        with system():
            User(email="ann@example.com", name="Ann", roles=["admin"], email_verified=True,
                 password_hash=hash_password("correct horse battery")).save()
        for scheme in ("light", "dark"):
            pg = audit.page(color_scheme=scheme)
            pg.goto(base + "/")
            problems += [f"/ ({scheme}): {p}" for p in audit(pg)]
            pg.goto(base + "/login")
            pg.fill("input[name=email]", "ann@example.com")
            pg.fill("input[name=password]", "correct horse battery")
            pg.click("button[type=submit]")
            pg.wait_for_url(base + "/")          # signed in; loading /projects sooner can cancel the POST
            pg.goto(base + "/projects")
            ready(pg)
            settle(pg)
            problems += [f"/projects ({scheme}): {p}" for p in audit(pg)]
            if scheme == "light":
                pg.click("#new-project")
                pg.fill("input[name=name]", "Launch")
                pg.locator("dialog button[type=submit]").click()
                pg.wait_for_url("**/projects/*")
                ready(pg)
                for title in ("Write docs", "Ship it"):
                    pg.fill("input[name=title]", title)
                    pg.locator("form button[type=submit]").click()
                    pg.wait_for_timeout(300)
                project = pg.url
            else:
                pg.goto(project)
                ready(pg)
            settle(pg)
            problems += [f"project ({scheme}): {p}" for p in audit(pg)]
    assert not problems, "\n".join(problems)
