"""Auth + security: password hashing, sessions, RBAC, OAuth, magic links,
TOTP, placement boundary, XSS, traversal, uploads, redirects, scanner."""

import base64
import json
import time

import pytest


def test_password_hash_and_verify():
    from pyweb import auth
    stored = auth.hash_password("s3cr3t-p@ss")
    assert stored != "s3cr3t-p@ss"
    assert stored.startswith("pbkdf2_sha256$")
    assert auth.verify_password("s3cr3t-p@ss", stored) is True
    assert auth.verify_password("wrong-pass", stored) is False


def test_password_verify_timing_safe():
    import pyweb.auth as auth_mod
    import inspect
    src = inspect.getsource(auth_mod.verify_password)
    assert "compare_digest" in src, "verify must use timing-safe compare"


def test_password_hash_uses_random_salt():
    from pyweb import auth
    assert auth.hash_password("same") != auth.hash_password("same")


def test_session_cookie_format_and_tamper_rejection():
    from pyweb import auth
    secret = "test-secret-key"
    cookie = auth.issue_session({"sub": "u1", "roles": ["user"]}, secret)
    body, sig = cookie.split(".")
    json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    assert len(sig) == 64
    assert auth.verify_session(cookie, secret)["sub"] == "u1"
    tampered = base64.urlsafe_b64encode(
        json.dumps({"sub": "admin"}).encode()).decode().rstrip("=") + "." + sig
    assert auth.verify_session(tampered, secret) is None
    assert auth.verify_session(cookie, "wrong-secret") is None


def test_session_expiry_enforced():
    from pyweb import auth
    secret = "test-secret-key"
    cookie = auth.issue_session({"sub": "u1"}, secret, max_age=60)
    assert auth.verify_session(cookie, secret, max_age=60)["sub"] == "u1"
    assert auth.verify_session(cookie, secret, max_age=-1) is None


def test_required_page_guard_blocks_anonymous():
    from pyweb import auth

    @auth.required
    def dashboard(session=None):
        return "ok"

    with pytest.raises(auth.NotAuthenticated):
        dashboard(session=None)
    assert dashboard(session={"sub": "u1"}) == "ok"


def test_permission_rbac_denies_without_role():
    from pyweb import auth

    @auth.permission("admin")
    def wipe(session=None):
        return "wiped"

    with pytest.raises(auth.Forbidden):
        wipe(session={"sub": "u1", "roles": ["user"]})
    assert wipe(session={"sub": "root", "roles": ["admin"]}) == "wiped"


def test_oauth_authorize_url_google_and_github():
    from pyweb import auth
    g = auth.OAuthClient("google", "cid", "csec", "https://app.example/cb")
    url = g.authorize_url("state123")
    assert "accounts.google.com" in url
    assert "client_id=cid" in url and "state=state123" in url
    assert "code" in url
    gh = auth.OAuthClient("github", "cid", "csec", "https://app.example/cb")
    assert "github.com/login/oauth/authorize" in gh.authorize_url("s")


def test_oauth_exchange_code_uses_urllib_no_extra_deps(monkeypatch):
    import urllib.request
    from pyweb import auth
    seen = {}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"access_token": "tok", "token_type": "bearer"}'

    def fake_urlopen(req, timeout=10):
        seen["url"] = req.full_url
        seen["data"] = req.data
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = auth.OAuthClient("github", "cid", "csec", "https://app.example/cb")
    tok = client.exchange_code("authcode1")
    assert tok["access_token"] == "tok"
    assert b"authcode1" in seen["data"]


def test_magic_link_token_expiry():
    from pyweb import auth
    secret = "magic-secret"
    tok = auth.issue_magic_token(secret, "a@example.com", ttl=60)
    assert auth.verify_magic_token(secret, tok) == "a@example.com"
    assert auth.verify_magic_token(secret, tok + "x") is None
    expired = auth.issue_magic_token(secret, "a@example.com", ttl=-1)
    assert auth.verify_magic_token(secret, expired) is None


def test_totp_rfc6238_vector():
    from pyweb import auth
    secret = b"12345678901234567890"
    assert auth.totp(secret, for_time=59) == "287082"
    assert auth.verify_totp(secret, "287082", for_time=59) is True
    assert auth.verify_totp(secret, "000000", for_time=59) is False


def test_placement_blocks_server_secret_in_browser_bundle():
    from pyweb.compiler import placement
    src = "from app import settings\nAPI_KEY = settings.SECRET_KEY\n"
    with pytest.raises(placement.CompileError) as ei:
        placement.check_source(src, filename="pages/home.py", placement="browser")
    assert "pages/home.py:2" in str(ei.value)


def test_placement_blocks_server_only_import_in_browser():
    from pyweb.compiler import placement
    for src in ("import sqlite3\nx = 1\n",
                "import psycopg\nx = 1\n",
                "import os\nk = os.environ['DB_PASSWORD']\n"):
        with pytest.raises(placement.CompileError) as ei:
            placement.check_source(src, filename="pages/p.py", placement="browser")
        assert "pages/p.py" in str(ei.value)
        assert "leak" in str(ei.value).lower()


def test_placement_blocks_file_read_in_browser():
    from pyweb.compiler import placement
    with pytest.raises(placement.CompileError):
        placement.check_source("data = open('/etc/secrets.txt').read()\n",
                               filename="pages/p.py", placement="browser")


def test_placement_secret_patterns_exist_and_server_passes():
    from pyweb.compiler import placement
    assert len(placement.SECRET_PATTERNS) >= 3
    src = "import sqlite3\nDB = 'x'\n"
    assert placement.check_source(src, filename="srv.py", placement="server") == "server"


XSS_PAYLOADS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "\"><script>alert(1)</script>",
    "' onmouseover='alert(1)",
]


@pytest.mark.parametrize("payload", XSS_PAYLOADS)
def test_ssr_escapes_text_nodes(payload):
    from pyweb import security
    out = security.ssr("<p>{{ body }}</p>", body=payload)
    assert "<script>" not in out
    assert payload not in out


@pytest.mark.parametrize("payload", XSS_PAYLOADS)
def test_ssr_escapes_attribute_values(payload):
    from pyweb import security
    out = security.ssr('<a title="{{ t }}">x</a>', t=payload)
    assert payload not in out
    assert "&quot;" in out or "&#x27;" in out or "&lt;" in out


def test_escape_attr_neutralizes_quote_breakout():
    from pyweb import security
    assert '"' not in security.escape_attr('"><script>alert(1)</script>')


def test_static_path_traversal_blocked():
    from pyweb import security
    with pytest.raises(security.PathTraversalError):
        security.safe_join("/srv/static", "../../etc/passwd")
    assert security.safe_join("/srv/static", "img/logo.png").endswith("img/logo.png")
    assert "static root" in (security.STATIC_DOC or "").lower()


def test_upload_rejects_bad_extension_and_magic_bytes():
    from pyweb import uploads
    with pytest.raises(uploads.UploadRejected):
        uploads.validate_content("shell.php", b"<?php evil(); ?>")
    with pytest.raises(uploads.UploadRejected):
        uploads.validate_content("fake.png", b"not-a-png-at-all........")
    ok = uploads.validate_content("logo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    assert ok.filename == "logo.png"


def test_upload_rejects_oversize():
    from pyweb import uploads
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (uploads.MAX_BYTES + 1)
    with pytest.raises(uploads.UploadRejected):
        uploads.validate_content("big.png", big)


def test_open_redirect_guard_on_next_param():
    from pyweb import security
    assert security.is_safe_redirect("/dashboard") is True
    assert security.is_safe_redirect("https://evil.example/phish") is False
    assert security.is_safe_redirect("//evil.example/") is False
    assert security.safe_next("https://evil.example/", "/home") == "/home"


def test_error_responses_never_leak_tracebacks():
    from pyweb import security
    try:
        raise ValueError("boom\nsecret=db-password traceback line")
    except ValueError as exc:
        body = security.safe_error(exc)
    assert "Traceback" not in body
    assert "db-password" not in body


def test_forms_validation_hardening():
    from pyweb import forms
    errs = forms.validate_request({"email": "not-an-email", "name": "x" * 500},
                                  {"email": ["required", "email"],
                                   "name": ["required", "max_length:50"]})
    assert "email" in errs and "name" in errs
    assert forms.validate_request({"email": "a@b.com", "name": "ok"},
                                  {"email": ["required", "email"]}) == {}


def test_security_scan_reports_findings():
    from pyweb import security
    app = {"routes": [
        {"path": "/transfer", "methods": ["POST"], "auth": False},
        {"path": "/x", "methods": ["GET"], "handler_source":
         "return '<div>' + name + '</div>'"},
        {"path": "/k", "methods": ["GET"], "handler_source":
         "KEY = os.environ['SECRET']"},
    ]}
    findings = security.scan(app)
    kinds = {f["kind"] for f in findings}
    assert "secret-leak" in kinds
    assert "xss-risk" in kinds
    assert "missing-auth-on-mutating-rpc" in kinds


def test_cli_security_scan_flag_exists():
    from pyweb import cli
    parser = cli.build_parser()
    args = parser.parse_args(["--security-scan"])
    assert args.security_scan is True
