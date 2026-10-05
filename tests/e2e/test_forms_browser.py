"""<Form> in a real browser: checks while typing, submit without reload, server errors
next to inputs, and the same form working with JavaScript turned off."""

import pytest

from pyweb import models as M
from pyweb.testing import serve

from .conftest import ready, until

APP = '''
from pyweb import App, Form, Input, Checkbox, Submit, Field, Model, server
from pyweb.models import Email, ValidationError

app = App(database="sqlite:///{db}")

class Signup(Model):
    name: str = Field(min=2, max=20)
    email: Email = Field(unique=True)
    news: bool = False

@server
def join(signup: Signup):
    if signup.name.lower() == "admin":
        raise ValidationError({{"name": "is reserved"}})
    signup.save()
    return signup

@app.page("/")
def Home():
    count = Signup.count()
    <h1>Join</h1>
    <p id="count">{{count}}</p>
    <Form action={{join}} redirect="/thanks/{{id}}">
        <Input name="name" />
        <Input name="email" />
        <Checkbox name="news" label="Send me news" />
        <Submit>Join</Submit>
    </Form>

@app.page("/thanks/{{n}}")
def Thanks(n: int):
    <p id="thanks">Thanks #{{n}}</p>
'''


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(M._state, "db", None)
    (tmp_path / "app.pyweb").write_text(APP.format(db=tmp_path / "f.db"))
    with serve(str(tmp_path / "app.pyweb"), rate_limit=False) as url:
        yield url
    M._state.db = None


def text(page, sel):
    return page.inner_text(sel).strip()


def test_browser_checks_while_typing_and_submits_without_reload(page, site):
    page.goto(site + "/")
    ready(page)
    page.fill("#pw-join-name", "A")
    page.press("#pw-join-name", "Tab")
    until(page, "document.getElementById('pw-join-name-error').hidden === false")
    assert text(page, "#pw-join-name-error") == "Name must be at least 2 characters"
    page.fill("#pw-join-name", "Ada")
    until(page, "document.getElementById('pw-join-name-error').hidden === true")
    page.fill("#pw-join-email", "not-an-email")
    page.click("button[type=submit]")
    until(page, "!document.getElementById('pw-join-email-error').hidden")
    assert text(page, "#pw-join-email-error") == "Email must be a valid email address"
    until(page, "document.activeElement.id === 'pw-join-email'")      # the first problem gets focus
    page.evaluate("window.__marker = 1")                    # gone if the page reloads
    page.fill("#pw-join-email", "ada@example.com")
    page.check("#pw-join-news")
    with page.expect_request(lambda r: "/__pyweb/form/join" in r.url):
        page.click("button[type=submit]")
    page.wait_for_selector("#thanks")
    assert text(page, "#thanks") == "Thanks #1" and page.evaluate("window.__marker") == 1


def test_server_errors_show_next_to_the_input(page, site):
    page.goto(site + "/")
    ready(page)
    page.fill("#pw-join-name", "admin")
    page.fill("#pw-join-email", "x@example.com")
    page.click("button[type=submit]")
    until(page, "!document.getElementById('pw-join-name-error').hidden")
    assert text(page, "#pw-join-name-error") == "Name is reserved"
    assert page.get_attribute("#pw-join-name", "aria-invalid") == "true"


def test_double_submit_runs_the_action_once(page, site):
    page.goto(site + "/")
    ready(page)
    page.fill("#pw-join-name", "Bob")
    page.fill("#pw-join-email", "bob@example.com")
    page.evaluate("""() => { const f = document.querySelector('form'); f.requestSubmit(); f.requestSubmit(); }""")
    page.wait_for_selector("#thanks")
    page.goto(site + "/")
    assert text(page, "#count") == "1"


def test_forms_work_without_javascript(browser, site):
    ctx = browser.new_context(java_script_enabled=False)
    page = ctx.new_page()
    try:
        page.goto(site + "/")
        page.fill("#pw-join-email", "al@example.com")
        page.fill("#pw-join-name", "admin")
        page.click("button[type=submit]")
        page.wait_for_load_state()
        assert "Name is reserved" in page.content()
        assert page.input_value("#pw-join-email") == "al@example.com"     # what was typed is kept
        page.fill("#pw-join-name", "Alan")
        page.click("button[type=submit]")
        page.wait_for_selector("#thanks")
        assert "Thanks #1" in page.content()
    finally:
        ctx.close()
