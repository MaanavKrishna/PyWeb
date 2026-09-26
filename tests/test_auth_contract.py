"""QA-006: protected-route session/redirect contract.

- Unauthenticated page access -> 302 Location: /login?next=<path>
- Cookie session -> session returned, no redirect
- Authorization: Bearer session -> session returned, no redirect
- login_response issues HttpOnly cookie + 302; logout clears it.
"""

from pyweb import auth
from pyweb.runtime.server import Request

SECRET = "test-secret-for-contract"


def _req(path="/dashboard", **kw):
    return Request("GET", path, **kw)


def test_unauthenticated_redirects_to_login():
    session, redirect = auth.require_session(_req("/dashboard"), SECRET)
    assert session is None
    assert redirect.status == 302
    assert redirect.headers["Location"] == "/login?next=/dashboard"


def test_cookie_session_authenticates():
    token = auth.issue_session({"sub": "u1"}, SECRET)
    session, redirect = auth.require_session(
        _req("/dashboard", cookies={"pyweb_session": token}), SECRET)
    assert redirect is None
    assert session["sub"] == "u1"


def test_bearer_session_authenticates():
    token = auth.issue_session({"sub": "u2"}, SECRET)
    session, redirect = auth.require_session(
        _req("/dashboard", headers={"Authorization": f"Bearer {token}"}), SECRET)
    assert redirect is None
    assert session["sub"] == "u2"


def test_tampered_session_redirects():
    token = auth.issue_session({"sub": "u1"}, SECRET) + "tampered"
    session, redirect = auth.require_session(
        _req(cookies={"pyweb_session": token}), SECRET)
    assert session is None and redirect.status == 302


def test_session_without_sub_redirects():
    token = auth.issue_session({"name": "nosub"}, SECRET)
    session, redirect = auth.require_session(
        _req(cookies={"pyweb_session": token}), SECRET)
    assert session is None and redirect.status == 302


def test_login_response_sets_cookie_and_redirects():
    res = auth.login_response("u1", SECRET, next_url="/dashboard")
    assert res.status == 302
    assert res.headers["Location"] == "/dashboard"
    cookie = res.headers["Set-Cookie"]
    assert cookie.startswith("pyweb_session=") and "HttpOnly" in cookie
    token = cookie.split("=", 1)[1].split(";", 1)[0]
    session, redirect = auth.require_session(
        _req("/dashboard", cookies={"pyweb_session": token}), SECRET)
    assert session["sub"] == "u1" and redirect is None


def test_logout_clears_cookie():
    res = auth.logout_response()
    assert res.status == 302
    assert "Max-Age=0" in res.headers["Set-Cookie"]
