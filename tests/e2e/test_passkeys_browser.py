"""Passkeys in a real browser: Chromium's virtual authenticator registers a passkey on
/account, then signs in with it on /login (no password typed)."""

import pytest

from pyweb import auth as pyauth
from pyweb import models as M
from pyweb.testing import serve

from .conftest import until

APP = '''
from pyweb import App

app = App(database="sqlite:///{db}", title="Keys")
auth = app.use_auth(signup="instant")

@app.page("/")
def Home():
    <p id="home">home</p>

@app.page("/dashboard", login=True)
def Dash():
    me = auth.user().email
    <p id="me">{{me}}</p>
'''


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(M._state, "db", None)
    monkeypatch.setattr(pyauth, "_versions", None)
    monkeypatch.setitem(pyauth.KIT, "kit", None)
    monkeypatch.setenv("PYWEB_BREACHED_PASSWORDS", "0")
    (tmp_path / "app.pyweb").write_text(APP.format(db=tmp_path / "k.db"))
    with serve(str(tmp_path / "app.pyweb"), rate_limit=False) as url:
        # WebAuthn needs a domain name, not an IP address, for the relying party id.
        yield url.replace("127.0.0.1", "localhost")
    M._state.db = None


def authenticator(page):
    cdp = page.context.new_cdp_session(page)
    cdp.send("WebAuthn.enable")
    cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {
        "protocol": "ctap2", "transport": "internal", "hasResidentKey": True,
        "hasUserVerification": True, "isUserVerified": True, "automaticPresenceSimulation": True}})
    return cdp


def register(page, site):
    authenticator(page)
    page.goto(site + "/signup")
    page.fill('input[name="name"]', "Ada")
    page.fill('input[name="email"]', "ada@example.com")
    page.fill('input[name="password"]', "correct horse battery")
    with page.expect_navigation():
        page.click("button:has-text('Create account')")
    page.goto(site + "/account")
    page.fill('input[name="passkey_name"]', "Laptop")
    page.click("[data-pw-passkey-add]")
    page.wait_for_selector("text=Laptop", timeout=8000)
    assert "No passkeys yet" not in page.content()
    with page.expect_navigation():
        page.click('form[action="/logout"] button')


def test_passkey_from_the_email_fields_autofill(page, site):
    register(page, site)
    page.goto(site + "/dashboard")                          # -> /login?next=/dashboard
    # The browser offers the saved passkey in the email field; the virtual authenticator
    # picks it at once, as a person tapping the suggestion would.
    page.wait_for_selector("#me", timeout=8000)
    assert page.inner_text("#me") == "ada@example.com"
    until(page, "location.pathname === '/dashboard'")


def test_passkey_from_the_button(page, site):
    register(page, site)
    # A browser without passkey autofill: only the button signs in.
    page.add_init_script("delete PublicKeyCredential.isConditionalMediationAvailable")
    page.goto(site + "/dashboard")
    assert "/login" in page.url
    page.wait_for_selector("[data-pw-passkey-login]:visible")
    page.click("[data-pw-passkey-login]", no_wait_after=True)
    page.wait_for_selector("#me", timeout=8000)
    assert page.inner_text("#me") == "ada@example.com"


def test_cancelled_passkey_shows_a_message(page, site):
    authenticator(page)
    page.goto(site + "/signup")
    page.fill('input[name="email"]', "bob@example.com")
    page.fill('input[name="password"]', "correct horse battery")
    with page.expect_navigation():
        page.click("button:has-text('Create account')")
    page.goto(site + "/account")
    # What the browser reports when the person closes its passkey dialog.
    page.evaluate("() => { navigator.credentials.create = () => "
                  "Promise.reject(new DOMException('closed', 'NotAllowedError')); }")
    page.click("[data-pw-passkey-add]")
    page.wait_for_selector("[data-pw-passkey-status]:not([hidden])")
    assert page.inner_text("[data-pw-passkey-status]") == "Cancelled."
