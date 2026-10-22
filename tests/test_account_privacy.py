"""Download my data, delete my account."""

import json

from pyweb.models import system
from tests.test_authkit import PASSWORD, Browser  # noqa: F401
from tests.test_org_pages import person, site  # noqa: F401 - fixture


def test_export_holds_the_account_and_rows_that_point_at_it(site):  # noqa: F811
    ann = person(site, "ann@example.com", "Ann")
    ann.post("/orgs", {"action": "create", "name": "Acme"})
    status, headers, body = ann.get("/account/export")
    assert status == 200 and "attachment" in headers["Content-Disposition"]
    data = json.loads(body)
    assert data["account"]["email"] == "ann@example.com"
    assert "password_hash" not in data["account"] and "totp_secret" not in data["account"]
    assert data["rows"]["org_memberships"][0]["role"] == "owner"
    assert "auth_events" in data["rows"] and "auth_sessions" in data["rows"]


def test_deleting_an_account(site):  # noqa: F811
    from pyweb.authkit import AuthEvent, User
    from pyweb.authkit.orgs import Membership, Organization
    ann = person(site, "ann@example.com", "Ann")
    bo = person(site, "bo@example.com", "Bo")
    ann.post("/orgs", {"action": "create", "name": "Solo"})
    bo.post("/orgs", {"action": "create", "name": "Shared"})
    with system():
        shared = Organization.where(slug="shared").first()
        Membership.create(org=shared, user=User.where(email="ann@example.com").first(), role="member")

    _, _, html = ann.post("/account", {"action": "delete_account", "confirm": "wrong@example.com"})
    assert "Type your email address" in html
    status, _, html = ann.post("/account", {"action": "delete_account", "confirm": "ann@example.com"})
    assert status == 200 and "were deleted" in html
    with system():
        assert User.where(email="ann@example.com").first() is None
        assert Organization.where(slug="solo").first() is None          # owned alone: deleted too
        assert Organization.where(slug="shared").first() is not None    # Bo's stays, without Ann
        assert Membership.where(org_id=shared.id).count() == 1
        events = [e for e in AuthEvent.query() if e.kind == "account_deleted"]
        assert len(events) == 1 and events[0].email is None and events[0].detail.startswith("user-")
        assert not any(e.email == "ann@example.com" for e in AuthEvent.query())
    assert ann.get("/account")[0] == 303                                # signed out


def test_the_only_owner_of_a_team_must_hand_it_over_first(site):  # noqa: F811
    from pyweb.authkit import User
    from pyweb.authkit.orgs import Membership, Organization
    ann = person(site, "ann@example.com", "Ann")
    person(site, "bo@example.com", "Bo")
    ann.post("/orgs", {"action": "create", "name": "Acme"})
    with system():
        Membership.create(org=Organization.where(slug="acme").first(),
                          user=User.where(email="bo@example.com").first(), role="member")
    status, _, html = ann.post("/account", {"action": "delete_account", "confirm": "ann@example.com"})
    assert status == 422 and "only owner of Acme" in html
    with system():
        assert User.where(email="ann@example.com").first() is not None


def test_push_subscriptions_are_exported_and_deleted(site):  # noqa: F811
    import pytest
    pytest.importorskip("cryptography")
    from pyweb import push
    from pyweb.authkit import User
    push._ready.clear()
    ann = person(site, "ann@example.com", "Ann")
    with system():
        uid = User.where(email="ann@example.com").first().id
    p256dh = push.b64e(b"\x04" + b"\x01" * 64)
    push.subscribe("https://fcm.googleapis.com/fcm/send/ann", p256dh, push.b64e(b"a" * 16), user=uid)
    push.subscribe("https://fcm.googleapis.com/fcm/send/other", p256dh, push.b64e(b"a" * 16), user=uid + 1)
    data = json.loads(ann.get("/account/export")[2])
    [row] = data["rows"]["pyweb_push_subscriptions"]
    assert row["endpoint"].endswith("/ann") and "auth" not in row and "p256dh" not in row
    ann.post("/account", {"action": "delete_account", "confirm": "ann@example.com"})
    assert push.subscriptions(uid) == [] and len(push.subscriptions(uid + 1)) == 1
