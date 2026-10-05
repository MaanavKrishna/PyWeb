"""0.5 auth kit: sign-up, sign-in, guards, resets, magic links, 2FA, passkeys, OAuth,
the admin, row policies and the audit log."""

import base64
import hashlib
import json
import re
import struct
import urllib.parse

import pytest

from pyweb import auth as pyauth
from pyweb import mail
from pyweb import models as M
from pyweb.hosting import Site

APP = '''
from pyweb import App, server

app = App(database="sqlite:///{db}", title="Demo")
auth = app.use_auth(signup={signup!r}, providers={providers!r})

class Note(Model):
    text: str
    owner_id: int = 0

Note.policy(read=lambda user: Note.owner_id == (user.id if user else -1),
            write=lambda user, note: user is not None and note.owner_id == user.id)

@server(login=True)
def whoami() -> str:
    return auth.user().email

@server(roles=["admin"])
def admin_only() -> str:
    return "42"

@server(fresh=60)
def sensitive() -> str:
    return "ok"

@server(login=True)
def add_note(text: str) -> int:
    return Note.create(text=text, owner_id=auth.user().id).id

@server(login=True)
def notes() -> list:
    return [n.text for n in Note.query().order("id")]

@server(login=True)
def edit_note(note_id: int, text: str) -> str:
    with system():
        note = Note.get(note_id)
    note.text = text
    note.save()
    return "saved"

@app.layout("/staff", roles=["staff"])
def Staff(children):
    <main>{{children}}</main>

@app.page("/staff/home")
def StaffHome():
    <p>staff only</p>

@app.page("/")
def Home():
    <p>home</p>

@app.page("/dashboard", login=True)
def Dash():
    me = auth.user().email
    <p id="me">{{me}}</p>
'''

PASSWORD = "correct horse battery"


class Browser:
    def __init__(self, site):
        self.site = site
        self.cookies = {}
        self.csrf = None

    def req(self, method, path, data=None, *, accept="text/html", js=None):
        h = {"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items()), "Accept": accept,
             "Host": "localhost"}
        body = b""
        if data is not None:
            data = dict(data)
            data.setdefault("__pw_csrf", self.csrf or "")
            body = urllib.parse.urlencode(data).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        if js is not None:
            body = json.dumps(js).encode()
            h["Content-Type"] = "application/json"
            h["X-PW-CSRF"] = self.csrf or ""
        status, headers, raw = self.site.respond(method, path, h, body)
        for k, v in headers:
            if k.lower() == "set-cookie":
                name, _, rest = v.partition("=")
                value = rest.split(";")[0]
                if "Max-Age=0" in v or value == "":
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = value
        text = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
        found = re.search(r'name="__pw_csrf" value="([^"]+)"', text)
        if found:
            self.csrf = found.group(1)
        return status, dict(headers), text

    def get(self, path):
        return self.req("GET", path)

    def post(self, path, data=None):
        if self.csrf is None:
            self.get("/login")
        return self.req("POST", path, data or {})

    def rpc(self, name, **args):
        status, _, body = self.req("POST", f"/__pyweb/rpc/{name}", js={"args": args}, accept="application/json")
        return status, json.loads(body)


def errors(html):
    return re.findall(r'class="pw-(?:error|notice pw-error)"[^>]*>([^<]+)<', html)


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(M._state, "db", None)
    monkeypatch.setattr(pyauth, "_versions", None)
    monkeypatch.setitem(pyauth.KIT, "kit", None)
    monkeypatch.delenv("PYWEB_ENV", raising=False)
    monkeypatch.setenv("PYWEB_BREACHED_PASSWORDS", "0")
    mail.OUTBOX.clear()

    def build(signup="verify", providers=()):
        app = tmp_path / f"app_{signup}.pyweb"
        app.write_text("from pyweb import Model, Field\nfrom pyweb.models import system\n" +
                       APP.format(db=tmp_path / f"{signup}.db", signup=signup, providers=list(providers)))
        return Site(str(app), debug=True, rate_limit=False)
    yield build
    M._state.db = None


def kit_of(site):
    return site.app.app.auth


def signed_up(site, email="ada@example.com", name="Ada", roles=()):
    kit = kit_of(site)
    from pyweb.authkit import User
    user = User(email=email, name=name, roles=list(roles), email_verified=True,
                password_hash=__import__("pyweb.authkit.passwords", fromlist=["x"]).hash_password(PASSWORD))
    user.save()
    b = Browser(site)
    b.get("/login")
    status, headers, _ = b.post("/login", {"email": email, "password": PASSWORD, "action": "password"})
    assert status == 303, "sign-in failed"
    return b, user, kit


def last_link(kind):
    msg = mail.OUTBOX[-1]
    return urllib.parse.urlparse(re.search(rf"(http://\S+/{kind}/\S+)", msg.text).group(1)).path


# ---------------------------------------------------------------- sign-up

def test_signup_confirms_the_email_first(make):
    site = make()
    b = Browser(site)
    b.get("/signup")
    status, _, html = b.post("/signup", {"name": "Ada", "email": "ada@example.com", "password": "short"})
    assert status == 422 and "Password must be at least 10 characters" in html
    status, _, html = b.post("/signup", {"name": "Ada", "email": "Ada@Example.com", "password": PASSWORD})
    assert status == 200 and "Check your email" in html and mail.OUTBOX[-1].subject.startswith("Confirm")
    from pyweb.authkit import User
    assert User.count() == 0                                  # nothing until the link is used
    link = last_link("verify")
    status, headers, _ = b.get(link)
    assert status == 303
    user = User.first()
    assert user.email == "ada@example.com" and user.email_verified and user.name == "Ada"
    assert b.get("/dashboard")[0] == 200
    assert b.get(link)[0] == 410                              # links work once


def test_signup_with_a_taken_email_looks_the_same(make):
    site = make()
    signed_up(site)
    b = Browser(site)
    b.get("/signup")
    status, _, html = b.post("/signup", {"name": "Eve", "email": "ada@example.com", "password": PASSWORD})
    assert status == 200 and "Check your email" in html          # no hint that the account exists
    assert mail.OUTBOX[-1].subject.startswith("You already have an account")


def test_instant_signup_and_guards(make):
    site = make(signup="instant")
    b = Browser(site)
    status, headers, _ = b.get("/dashboard")
    assert status == 303 and headers["Location"] == "/login?next=%2Fdashboard"
    b.get("/signup")
    status, headers, _ = b.post("/signup", {"name": "Bo", "email": "bo@example.com", "password": PASSWORD})
    assert status == 303
    status, _, html = b.get("/dashboard")
    assert status == 200 and 'id="me">bo@example.com<' in html
    assert b.rpc("whoami") == (200, {"result": "bo@example.com"})
    assert b.rpc("admin_only")[0] == 403
    assert b.get("/staff/home")[0] == 403                    # the layout's roles= guard


def test_signed_out_server_functions_answer_401(make):
    site = make()
    b = Browser(site)
    assert b.rpc("whoami")[0] == 401
    assert b.rpc("sensitive")[0] == 401


# ----------------------------------------------------------------- sign-in

def test_wrong_password_and_unknown_email_get_the_same_answer(make):
    site = make()
    signed_up(site)
    b = Browser(site)
    b.get("/login")
    s1, _, h1 = b.post("/login", {"email": "ada@example.com", "password": "not the password", "action": "password"})
    s2, _, h2 = b.post("/login", {"email": "nobody@example.com", "password": "not the password", "action": "password"})
    assert s1 == s2 == 422 and errors(h1) == errors(h2) == ["That email and password don&#x27;t match."]


def test_guessing_is_slowed_down_not_locked(make):
    site = make()
    signed_up(site)
    b = Browser(site)
    b.get("/login")
    for _ in range(5):
        b.post("/login", {"email": "ada@example.com", "password": "guess guess guess", "action": "password"})
    status, _, html = b.post("/login", {"email": "ada@example.com", "password": PASSWORD, "action": "password"})
    assert status == 422 and "Too many attempts" in html
    from pyweb.authkit import AuthEvent
    assert AuthEvent.where(kind="login_failed").count() == 5 and AuthEvent.where(kind="login_throttled").exists()


def test_old_password_hashes_are_upgraded(make):
    site = make()
    from pyweb.authkit import User
    user = User.create(email="old@example.com", password_hash=pyauth.hash_password(PASSWORD))
    b = Browser(site)
    b.get("/login")
    assert b.post("/login", {"email": "old@example.com", "password": PASSWORD, "action": "password"})[0] == 303
    assert User.get(user.id).password_hash.startswith(("scrypt$", "$argon2"))


def test_next_must_stay_on_this_site(make):
    site = make()
    signed_up(site)
    b = Browser(site)
    b.get("/login")
    status, headers, _ = b.post("/login", {"email": "ada@example.com", "password": PASSWORD, "action": "password",
                                           "next": "https://evil.example/"})
    assert status == 303 and headers["Location"] == "/"


def test_logout_needs_a_post_with_a_token(make):
    site = make()
    b, _, _ = signed_up(site)
    status, _, html = b.get("/logout")
    assert status == 200 and b.get("/dashboard")[0] == 200     # a GET only shows a button
    assert b.req("POST", "/logout", {"__pw_csrf": "forged"})[0] == 403
    assert b.post("/logout")[0] == 303 and b.get("/dashboard")[0] == 303


# ------------------------------------------------------------------ resets

def test_password_reset_signs_out_other_devices(make):
    site = make()
    phone, user, _ = signed_up(site)
    laptop = Browser(site)
    laptop.get("/reset")
    count = len(mail.OUTBOX)
    status, _, html = laptop.post("/reset", {"email": "nobody@example.com"})
    assert status == 200 and "If an account uses that email" in html and len(mail.OUTBOX) == count
    laptop.post("/reset", {"email": "ada@example.com"})
    link = last_link("reset")
    status, _, html = laptop.get(link)
    assert status == 200 and "New password" in html
    assert laptop.post(link, {"password": "short"})[0] == 422
    status, headers, _ = laptop.post(link, {"password": "a brand new passphrase"})
    assert status == 303
    assert laptop.get("/dashboard")[0] == 200                     # this device is signed in
    assert phone.get("/dashboard")[0] == 303                      # every other session ended
    assert laptop.get(link)[0] == 410                             # the link is used up
    assert kit_of(site).authenticate("ada@example.com", "a brand new passphrase").id == user.id


def test_magic_links_need_a_click_and_work_once(make):
    site = make()
    signed_up(site)
    b = Browser(site)
    b.get("/login")
    status, _, html = b.post("/login", {"email": "ada@example.com", "action": "magic"})
    assert status == 200 and "Check your email" in html
    link = last_link("magic")
    status, _, html = b.get(link)                                 # a mail scanner's visit signs nobody in
    assert status == 200 and b.get("/dashboard")[0] == 303
    assert b.post(link)[0] == 303 and b.get("/dashboard")[0] == 200
    other = Browser(site)
    other.get(link)
    assert other.post(link)[0] == 410


# --------------------------------------------------------------------- 2FA

def test_two_step_sign_in(make):
    site = make()
    b, user, kit = signed_up(site)
    _, _, html = b.post("/account", {"action": "totp_start"})
    secret = re.search(r'class="pw-code">([A-Z2-7 ]+)<', html).group(1).replace(" ", "")
    setup = re.search(r'name="setup" value="([^"]+)"', html).group(1)
    assert b.post("/account", {"action": "totp_confirm", "setup": setup, "code": "000000"})[0] == 422
    code = pyauth.totp(base64.b32decode(secret))
    status, _, html = b.post("/account", {"action": "totp_confirm", "setup": setup, "code": code})
    assert status == 200 and "Two-step sign-in is on" in html
    recovery = re.findall(r'class="pw-code">([0-9a-f ]+)<', html)[-1].split()
    assert len(recovery) == 10
    b.post("/logout")
    b.get("/login")
    status, headers, _ = b.post("/login", {"email": "ada@example.com", "password": PASSWORD, "action": "password"})
    assert headers["Location"] == "/login/2fa" and b.get("/dashboard")[0] == 303   # not signed in yet
    assert b.post("/login/2fa", {"code": "123456"})[0] == 422
    assert b.post("/login/2fa", {"code": pyauth.totp(base64.b32decode(secret))})[0] == 303
    assert b.get("/dashboard")[0] == 200
    b.post("/logout")
    b.get("/login")
    b.post("/login", {"email": "ada@example.com", "password": PASSWORD, "action": "password"})
    assert b.post("/login/2fa", {"code": recovery[0]})[0] == 303         # a recovery code, once
    b.post("/logout")
    b.get("/login")
    b.post("/login", {"email": "ada@example.com", "password": PASSWORD, "action": "password"})
    assert b.post("/login/2fa", {"code": recovery[0]})[0] == 422


def test_account_changes_need_a_recent_sign_in(make, monkeypatch):
    site = make()
    b, user, kit = signed_up(site)
    import time as _time
    real = _time.time
    monkeypatch.setattr(_time, "time", lambda: real() + 3600)
    status, headers, _ = b.post("/account", {"action": "totp_start"})
    assert status == 303 and "fresh=1" in headers["Location"]


def test_password_change_keeps_this_device_only(make):
    site = make()
    b, user, kit = signed_up(site)
    other = Browser(site)
    other.get("/login")
    other.post("/login", {"email": "ada@example.com", "password": PASSWORD, "action": "password"})
    assert b.post("/account", {"action": "password", "current": "wrong one!!", "new": "another passphrase"})[0] == 422
    status, _, html = b.post("/account", {"action": "password", "current": PASSWORD, "new": "another passphrase"})
    assert status == 200 and "Password changed" in html
    assert b.get("/dashboard")[0] == 200 and other.get("/dashboard")[0] == 303


# ---------------------------------------------------------------- passkeys

def _cbor(value):
    """Just enough CBOR encoding for a fake authenticator."""
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([major << 5 | info]) + n.to_bytes(size, "big")
    if isinstance(value, bool):
        return bytes([0xF5 if value else 0xF4])
    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, bytes):
        return head(2, len(value)) + value
    if isinstance(value, str):
        return head(3, len(value.encode())) + value.encode()
    if isinstance(value, dict):
        return head(5, len(value)) + b"".join(_cbor(k) + _cbor(v) for k, v in value.items())
    raise TypeError(value)


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class FakeAuthenticator:
    def __init__(self, rp_id="localhost", origin="http://localhost"):
        ec = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ec")
        self.ec = ec
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = b"credential-id-0123456789"
        self.rp_id, self.origin = rp_id, origin
        self.count = 0

    def _auth_data(self, flags, extra=b""):
        self.count += 1
        return hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", self.count) + extra

    def create(self, options):
        nums = self.key.public_key().public_numbers()
        cose = _cbor({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")})
        attested = b"\x00" * 16 + struct.pack(">H", len(self.cred_id)) + self.cred_id + cose
        auth_data = self._auth_data(0x45, attested)
        client = json.dumps({"type": "webauthn.create", "challenge": options["publicKey"]["challenge"],
                             "origin": self.origin}).encode()
        return {"id": _b64(self.cred_id), "attestationObject": _b64(_cbor({"fmt": "none", "attStmt": {},
                                                                           "authData": auth_data})),
                "clientDataJSON": _b64(client), "name": "Test key"}

    def get(self, options, cred_id=None):
        from cryptography.hazmat.primitives import hashes
        auth_data = self._auth_data(0x05)
        client = json.dumps({"type": "webauthn.get", "challenge": options["publicKey"]["challenge"],
                             "origin": self.origin}).encode()
        sig = self.key.sign(auth_data + hashlib.sha256(client).digest(), self.ec.ECDSA(hashes.SHA256()))
        return {"id": cred_id or _b64(self.cred_id), "authenticatorData": _b64(auth_data),
                "clientDataJSON": _b64(client), "signature": _b64(sig), "next": "/dashboard"}


def test_passkey_register_and_sign_in(make):
    site = make()
    b, user, kit = signed_up(site)
    device = FakeAuthenticator()
    _, _, body = b.req("POST", "/auth/passkey/register/options", js={}, accept="application/json")
    options = json.loads(body)
    assert options["publicKey"]["rp"]["id"] == "localhost"
    status, _, body = b.req("POST", "/auth/passkey/register", js=device.create(options), accept="application/json")
    assert status == 200, body
    from pyweb.authkit import Credential
    assert Credential.where(user=user).count() == 1
    fresh = Browser(site)
    fresh.get("/login")
    _, _, body = fresh.req("POST", "/auth/passkey/login/options", js={}, accept="application/json")
    status, _, body = fresh.req("POST", "/auth/passkey/login", js=device.get(json.loads(body)),
                                accept="application/json")
    assert status == 200 and json.loads(body)["redirect"] == "/dashboard"
    assert fresh.get("/dashboard")[0] == 200
    # replaying the same signed response fails (the challenge is single-use)
    replay = Browser(site)
    replay.get("/login")
    _, _, body = replay.req("POST", "/auth/passkey/login/options", js={}, accept="application/json")
    opts = json.loads(body)
    good = device.get(opts)
    bad = dict(good, signature=_b64(b"\x30\x06\x02\x01\x01\x02\x01\x01"))
    assert replay.req("POST", "/auth/passkey/login", js=bad, accept="application/json")[0] == 400
    assert replay.req("POST", "/auth/passkey/login", js=good, accept="application/json")[0] == 400


def test_a_captured_passkey_sign_in_cant_be_replayed(make):
    """Synced passkeys always report a zero counter, so the challenge itself must be single-use,
    even when an attacker replays both the challenge cookie and the signed response."""
    site = make()
    b, user, kit = signed_up(site)
    device = FakeAuthenticator()
    _, _, body = b.req("POST", "/auth/passkey/register/options", js={}, accept="application/json")
    assert b.req("POST", "/auth/passkey/register", js=device.create(json.loads(body)),
                 accept="application/json")[0] == 200
    device._auth_data = lambda flags, extra=b"": (hashlib.sha256(b"localhost").digest() + bytes([flags]) +
                                                  struct.pack(">I", 0) + extra)
    victim = Browser(site)
    victim.get("/login")
    _, _, body = victim.req("POST", "/auth/passkey/login/options", js={}, accept="application/json")
    captured_cookies = dict(victim.cookies)
    response = device.get(json.loads(body))
    assert victim.req("POST", "/auth/passkey/login", js=response, accept="application/json")[0] == 200
    attacker = Browser(site)
    attacker.get("/login")
    attacker.cookies.update({k: v for k, v in captured_cookies.items() if k == "__pw_webauthn"})
    status, _, _ = attacker.req("POST", "/auth/passkey/login", js=response, accept="application/json")
    assert status == 400 and attacker.get("/dashboard")[0] == 303


def test_passkey_from_another_site_is_refused(make):
    site = make()
    b, user, kit = signed_up(site)
    device = FakeAuthenticator(origin="http://evil.example")
    _, _, body = b.req("POST", "/auth/passkey/register/options", js={}, accept="application/json")
    status, _, body = b.req("POST", "/auth/passkey/register", js=device.create(json.loads(body)),
                            accept="application/json")
    assert status == 400 and "origin" in body


# ------------------------------------------------------------------- OAuth

class FakeProvider:
    def __init__(self, info):
        self.info = info

    def __call__(self, url, *, data=None, token=None, timeout=10):
        if data is not None:
            return {"access_token": "tok"}
        if url.endswith("/user"):
            return {"id": self.info["subject"], "name": self.info.get("name"), "login": "octo"}
        if url.endswith("/user/emails"):
            return [{"email": self.info["email"], "primary": True, "verified": self.info["verified"]}]
        return {}


def _oauth(b, kit, info, next_url="/dashboard"):
    prov = kit.providers["github"]
    prov.http = FakeProvider(info)
    status, headers, _ = b.get(f"/auth/github/start?next={urllib.parse.quote(next_url)}")
    assert status == 303 and headers["Location"].startswith("https://github.com/login/oauth/authorize?")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)
    assert q["code_challenge_method"] == ["S256"]
    return b.get(f"/auth/github/callback?code=abc&state={q['state'][0]}")


def test_oauth_sign_up_link_and_no_silent_takeover(make, monkeypatch):
    monkeypatch.setenv("PYWEB_OAUTH_GITHUB_ID", "id")
    monkeypatch.setenv("PYWEB_OAUTH_GITHUB_SECRET", "secret")
    site = make(providers=("github",))
    kit = kit_of(site)
    b = Browser(site)
    status, headers, _ = _oauth(b, kit, {"subject": "1", "email": "new@example.com", "verified": True})
    assert status == 303 and headers["Location"] == "/dashboard" and b.get("/dashboard")[0] == 200
    signed_up(site, email="ada@example.com")
    stranger = Browser(site)
    status, _, html = _oauth(stranger, kit, {"subject": "2", "email": "ada@example.com", "verified": True})
    assert status == 400 and "already exists" in html            # never attached without Ada signing in
    status, _, html = _oauth(Browser(site), kit, {"subject": "3", "email": "x@example.com", "verified": False})
    assert status == 400 and "no verified email" in html
    ada, user, _ = signed_up(site, email="ada2@example.com")
    status, headers, _ = _oauth(ada, kit, {"subject": "4", "email": "whatever@example.com", "verified": True},
                                next_url="/account")
    from pyweb.authkit import Identity
    assert status == 303 and Identity.where(user=user, provider="github").exists()
    status, _, _ = b.get("/auth/github/callback?code=abc&state=forged")
    assert status == 400


# ------------------------------------------------------------------- admin

def test_admin_lists_edits_and_respects_private_fields(make):
    site = make()
    member, user, kit = signed_up(site, email="member@example.com")
    assert member.get("/admin")[0] == 403
    admin, boss, _ = signed_up(site, email="boss@example.com", roles=["admin"])
    status, _, html = admin.get("/admin")
    assert status == 200 and 'href="/admin/users"' in html and 'href="/admin/auth_events"' in html
    status, _, html = admin.get("/admin/users?q=member")
    assert "member@example.com" in html and "boss@example.com" not in html and "password_hash" not in html
    status, _, html = admin.get(f"/admin/users/{user.id}")
    assert status == 200 and "password_hash" not in html and "Password hash" not in html
    assert "Session version" in html and 'name="session_version"' not in html   # shown, not editable
    status, headers, _ = admin.post(f"/admin/users/{user.id}", {"email": "member@example.com", "name": "Mem",
                                                                 "roles": '["staff"]', "__has_email_verified": "1",
                                                                 "__has_is_active": "1", "is_active": "true"})
    assert status == 303
    from pyweb.authkit import AuthEvent, User
    assert User.get(user.id).roles == ["staff"] and User.get(user.id).name == "Mem"
    assert member.get("/dashboard")[0] == 303                    # role change signed them out
    assert AuthEvent.where(kind="roles_changed").exists()
    assert admin.post(f"/admin/users/{user.id}", {"action": "delete"})[0] == 422
    assert admin.post(f"/admin/users/{user.id}", {"action": "delete", "confirm": "yes"})[0] == 303
    assert User.get(user.id) is None


# ------------------------------------------------------------- row policies

def test_row_policies_follow_the_signed_in_user(make):
    site = make()
    a, ua, _ = signed_up(site, email="a@example.com")
    b, ub, _ = signed_up(site, email="b@example.com")
    _, note_id = a.rpc("add_note", text="mine")
    note_id = note_id["result"]
    b.rpc("add_note", text="theirs")
    assert a.rpc("notes") == (200, {"result": ["mine"]})
    assert b.rpc("notes") == (200, {"result": ["theirs"]})
    status, body = b.rpc("edit_note", note_id=note_id, text="hacked")
    assert status == 403
    assert a.rpc("edit_note", note_id=note_id, text="edited")[1] == {"result": "saved"}
    Note = next(m for m in site.app.models() if m.__name__ == "Note")
    assert sorted(n.text for n in Note.all()) == ["edited", "theirs"]    # no request: no policy


def test_audit_log_records_activity(make):
    site = make()
    b, user, _ = signed_up(site)
    b.post("/logout")
    from pyweb.authkit import AuthEvent
    kinds = [e.kind for e in AuthEvent.where(user=user).order("id")]
    assert kinds[:2] == ["login", "logout"]
    assert AuthEvent.where(user=user).first().ip


def test_expired_tokens_are_refused(make):
    site = make()
    signed_up(site)
    from pyweb.authkit import AuthToken, User
    token = AuthToken.issue("reset", user=User.first(), ttl=-1)
    b = Browser(site)
    assert b.get(f"/reset/{token}")[0] == 410


def test_apps_can_replace_kit_pages(make, tmp_path, monkeypatch):
    site = make()
    src = (tmp_path / "app_verify.pyweb").read_text() + '\n@app.page("/login")\ndef MyLogin():\n    <p>custom</p>\n'
    (tmp_path / "custom.pyweb").write_text(src)
    custom = Site(str(tmp_path / "custom.pyweb"), debug=True, rate_limit=False)
    status, _, body = custom.respond("GET", "/login", {})
    assert status == 200 and b"custom" in body
    assert custom.respond("GET", "/reset", {})[0] == 200          # the rest stay
    _ = site


def test_emailed_links_never_trust_the_host_header_in_production(make, monkeypatch):
    site = make()
    kit = kit_of(site)
    monkeypatch.setenv("PYWEB_ENV", "production")
    monkeypatch.setenv("PYWEB_AUTH_SECRET", "x" * 40)
    monkeypatch.setattr(kit, "origin", None)
    with pytest.raises(RuntimeError, match="PYWEB_ORIGIN"):
        kit._link("/reset/abc")
    monkeypatch.setattr(kit, "origin", "https://acme.dev/")
    assert kit._link("/reset/abc") == "https://acme.dev/reset/abc"


def test_check_production_asks_for_an_origin(monkeypatch):
    from pyweb.cli import production_problems
    monkeypatch.delenv("PYWEB_ORIGIN", raising=False)
    src = "app = App()\nauth = app.use_auth()\n"
    assert any("PYWEB_ORIGIN" in p for p in production_problems(src))
    monkeypatch.setenv("PYWEB_ORIGIN", "https://acme.dev")
    assert not any("PYWEB_ORIGIN" in p for p in production_problems(src))
