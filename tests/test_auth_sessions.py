"""/account lists where you're signed in; each device can be signed out on its own."""

import re

from pyweb.authkit.models import AuthSession, device_label, ip_prefix
from pyweb.models import system
from tests.test_authkit import PASSWORD, Browser, signed_up
from tests.test_authkit import make  # noqa: F401 - the fixture

FIREFOX_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:131.0) Gecko/20100101 Firefox/131.0"
CHROME_ANDROID = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0.0.0 Mobile Safari/537.36")


def sign_in_again(site, email="ada@example.com"):
    b = Browser(site)
    b.get("/login")
    status, _, _ = b.post("/login", {"email": email, "password": PASSWORD, "action": "password"})
    assert status == 303
    return b


def signed_in(b):
    status, _, _ = b.get("/dashboard")
    return status == 200


def test_device_labels_and_address_prefixes():
    assert device_label(FIREFOX_MAC) == "Firefox on macOS"
    assert device_label(CHROME_ANDROID) == "Chrome on Android"
    assert device_label("") == "Unknown device"
    assert ip_prefix("203.0.113.77") == "203.0.113.0/24"
    assert ip_prefix("2001:db8:1234:5678::1") == "2001:db8:1234::/48"
    assert ip_prefix("not an ip") is None


def test_account_lists_sessions_and_signs_one_out(make):  # noqa: F811
    site = make()
    laptop, user, kit = signed_up(site)
    phone = sign_in_again(site)
    assert signed_in(laptop) and signed_in(phone)
    _, _, html = laptop.get("/account")
    assert html.count("This device") == 1 and "Where you" in html
    ids = re.findall(r'name="action" value="end_session"><input type="hidden" name="id" value="(\d+)"', html)
    assert len(ids) == 1                                   # the phone (the laptop is "This device")
    status, _, html = laptop.post("/account", {"action": "end_session", "id": ids[0]})
    assert status == 200 and "Signed out of that device" in html
    assert not signed_in(phone)                            # the phone's session is over
    assert signed_in(laptop)                               # the laptop's isn't
    _, _, html = laptop.post("/account", {"action": "end_session", "id": ids[0]})
    assert "already signed out" in html


def test_someone_elses_session_id_does_nothing(make):  # noqa: F811
    site = make()
    ada, _, _ = signed_up(site)
    bob, bob_user, _ = signed_up(site, email="bob@example.com", name="Bob")
    with system():
        bobs = AuthSession.where(user=bob_user).first()
    ada.post("/account", {"action": "end_session", "id": str(bobs.id)})
    assert signed_in(bob)


def test_sign_out_and_sign_out_everywhere_mark_rows(make):  # noqa: F811
    site = make()
    laptop, user, kit = signed_up(site)
    phone = sign_in_again(site)
    phone.post("/logout", {})
    with system():
        assert sum(1 for s in AuthSession.where(user=user) if s.revoked_at is not None) == 1
    tablet = sign_in_again(site)
    laptop.post("/account", {"action": "revoke"})
    assert not signed_in(tablet) and signed_in(laptop)
    with system():
        live = [s for s in AuthSession.where(user=user) if s.revoked_at is None]
    assert len(live) == 1                                  # only the laptop's new session


def test_sessions_without_the_table_still_work(make, monkeypatch):  # noqa: F811
    """An app upgraded from 0.5 before running its migration: sign-in and checks fall back."""
    site = make()
    laptop, user, kit = signed_up(site)
    from pyweb.authkit import User
    User._database().execute("DROP TABLE auth_sessions")
    assert signed_in(laptop)
    again = sign_in_again(site)
    assert signed_in(again)
    status, _, _ = again.get("/account")
    assert status == 200


def test_upgrade_reports_the_missing_migration(tmp_path):
    from pyweb.upgrade import check
    (tmp_path / "app.pyweb").write_text("from pyweb import App\napp = App()\nauth = app.use_auth()\n")
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001_initial.py").write_text("# create table users ...\n")
    assert "auth-sessions" in [f.code for f in check(str(tmp_path / "app.pyweb"))]
    (tmp_path / "migrations" / "0002_auth_sessions.py").write_text("# create table auth_sessions ...\n")
    assert "auth-sessions" not in [f.code for f in check(str(tmp_path / "app.pyweb"))]
