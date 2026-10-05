"""Unit tests for the auth kit's building blocks: hashing, throttling, CBOR, mail."""

import hashlib
import os
import time

import pytest

from pyweb import mail
from pyweb.authkit import passwords
from pyweb.authkit.throttle import Throttle
from pyweb.authkit.webauthn import b64url, cbor_loads, unb64url


@pytest.fixture(autouse=True)
def _cheap_scrypt(monkeypatch):
    monkeypatch.setattr(passwords, "SCRYPT", {"n": 2 ** 10, "r": 8, "p": 1})
    monkeypatch.setattr(passwords, "_argon2", lambda: None)
    monkeypatch.setattr(passwords, "_DUMMY", None)
    monkeypatch.delenv("PYWEB_BREACHED_PASSWORDS", raising=False)


# ----------------------------------------------------------------- passwords

def test_scrypt_round_trip_and_salting():
    a = passwords.hash_password("correct horse battery")
    b = passwords.hash_password("correct horse battery")
    assert a != b and a.startswith("scrypt$")
    assert passwords.verify_password("correct horse battery", a)
    assert not passwords.verify_password("correct horse batterY", a)
    assert not passwords.verify_password(None, a)
    assert not passwords.verify_password("x", "")


@pytest.mark.parametrize("stored", ["scrypt$", "scrypt$x$y$z$a$b", "scrypt$1024$8$1$!!$??", "$argon2id$v=19$junk"])
def test_garbage_hashes_never_verify(stored):
    assert passwords.verify_password("anything at all", stored) is False


def test_old_and_weaker_hashes_need_a_rehash(monkeypatch):
    from pyweb import auth
    old = auth.hash_password("legacy password!")
    assert passwords.verify_password("legacy password!", old)
    assert passwords.needs_rehash(old)
    current = passwords.hash_password("x" * 12)
    assert not passwords.needs_rehash(current)
    monkeypatch.setattr(passwords, "SCRYPT", {"n": 2 ** 11, "r": 8, "p": 1})
    assert passwords.needs_rehash(current)                # parameters went up
    assert not passwords.needs_rehash("")


def test_password_rules():
    assert passwords.problem("short") == "must be at least 10 characters"
    assert passwords.problem("x" * 513) == "must be at most 512 characters"
    assert passwords.problem("ada@example.com", email="ada@example.com") == "can't be your email or name"
    assert passwords.problem("Ada Lovelace", name="ada lovelace") == "can't be your email or name"
    assert passwords.problem("ten chars!") is None        # no composition rules


def test_breach_check_sends_only_a_hash_prefix(monkeypatch):
    seen = {}
    digest = hashlib.sha1(b"password1234").hexdigest().upper()

    class Resp:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.body.encode()

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        return Resp(f"0000000000000000000000000000000000A:3\r\n{digest[5:]}:42\r\n")

    monkeypatch.setattr(passwords.urllib.request, "urlopen", fake_urlopen)
    assert passwords.breached("password1234") == 42
    assert seen["url"].endswith("/range/" + digest[:5]) and digest[5:] not in seen["url"]
    monkeypatch.setenv("PYWEB_BREACHED_PASSWORDS", "1")
    assert passwords.problem("password1234") == "appears in a known data breach; choose another"


def test_breach_check_failing_never_blocks(monkeypatch):
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(passwords.urllib.request, "urlopen", boom)
    assert passwords.breached("whatever password") == 0


def test_breach_check_default_follows_the_environment(monkeypatch):
    monkeypatch.delenv("PYWEB_ENV", raising=False)
    assert not passwords.breach_check_enabled()
    monkeypatch.setenv("PYWEB_ENV", "production")
    assert passwords.breach_check_enabled()
    monkeypatch.setenv("PYWEB_BREACHED_PASSWORDS", "0")
    assert not passwords.breach_check_enabled()


def test_unknown_emails_take_as_long_as_real_ones():
    """No timing oracle: a failed check of a real hash and burn_time cost about the same."""
    real = passwords.hash_password("the real password")
    passwords.burn_time("warm up")

    def timed(fn, n=21):
        best = []
        for _ in range(n):
            t = time.perf_counter()
            fn()
            best.append(time.perf_counter() - t)
        best.sort()
        return best[n // 2]                                   # median

    known = timed(lambda: passwords.verify_password("a wrong guess", real))
    unknown = timed(lambda: passwords.burn_time("a wrong guess"))
    assert 0.5 < unknown / known < 2.0


# ------------------------------------------------------------------ throttle

def test_throttle_doubles_after_free_attempts(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    t = Throttle(free=3, cap=8, window=3600, redis_url="")
    for _ in range(3):
        assert t.wait("k") == 0
        t.fail("k")
    assert t.wait("k") == 1
    t.fail("k")
    assert t.wait("k") == 2
    for _ in range(5):
        t.fail("k")
    assert t.wait("k") == 8                                   # capped
    now[0] += 8
    assert t.wait("k") == 0
    assert t.wait("other") == 0                               # keys are separate
    t.clear("k")
    assert t.wait("k") == 0


def test_throttle_forgets_after_the_window(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    t = Throttle(free=2, cap=900, window=60, redis_url="")
    for _ in range(3):
        t.fail("k")
    assert t.wait("k") > 0
    now[0] += 61
    assert t.wait("k") == 0
    t.fail("k")                                               # the count starts again
    assert t.wait("k") == 0


def test_throttle_falls_back_when_redis_is_down():
    class Down:
        def __getattr__(self, name):
            def fail(*a, **k):
                raise ConnectionError("down")
            return fail
    t = Throttle(free=1, client=Down())
    t.fail("k")
    t.fail("k")
    assert t.wait("k") >= 1
    t.clear("k")
    assert t.wait("k") == 0


def test_throttle_shares_counts_through_redis():
    url = os.environ.get("PYWEB_TEST_REDIS")
    if not url:
        pytest.skip("set PYWEB_TEST_REDIS to run Redis tests")
    import redis
    redis_url = url
    client = redis.Redis.from_url(redis_url)
    a = Throttle(free=1, client=client, prefix="pyweb:test-throttle:")
    b = Throttle(free=1, client=client, prefix="pyweb:test-throttle:")
    a.clear("k")
    a.fail("k")
    a.fail("k")
    assert b.wait("k") >= 1
    b.clear("k")
    assert a.wait("k") == 0


# ---------------------------------------------------------------------- CBOR

@pytest.mark.parametrize("data,value", [
    (b"\x00", 0), (b"\x17", 23), (b"\x18\x18", 24), (b"\x19\x01\x00", 256), (b"\x1a\x00\x01\x00\x00", 65536),
    (b"\x20", -1), (b"\x38\x63", -100), (b"\x43\x01\x02\x03", b"\x01\x02\x03"), (b"\x63abc", "abc"),
    (b"\x82\x01\x82\x02\x03", [1, [2, 3]]), (b"\xa2\x61a\x01\x61b\x02", {"a": 1, "b": 2}),
    (b"\xf4", False), (b"\xf5", True), (b"\xf6", None),
])
def test_cbor_decodes_rfc_8949_examples(data, value):
    assert cbor_loads(data) == (value, len(data))


@pytest.mark.parametrize("data", [
    b"", b"\x18", b"\x43\x01", b"\x5f", b"\x9f", b"\xc0\x00", b"\xa1\x81\x01\x02",
    b"\x81" * 40 + b"\x00", b"\x99\xff\xff", b"\x62\xff\xfe",
])
def test_cbor_refuses_bad_or_hostile_input(data):
    with pytest.raises(ValueError):
        cbor_loads(data)


def test_cbor_fuzz_never_raises_anything_but_valueerror():
    import random
    rng = random.Random(5)
    for _ in range(3000):
        blob = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 24)))
        try:
            cbor_loads(blob)
        except ValueError:
            pass


def test_base64url_round_trip():
    for raw in (b"", b"\xff", b"\x00\x01\x02\x03\xfe"):
        text = b64url(raw)
        assert "=" not in text and "+" not in text and "/" not in text
        assert unb64url(text) == raw


# ---------------------------------------------------------------------- mail

def test_console_mail_keeps_messages(monkeypatch):
    monkeypatch.delenv("PYWEB_MAIL_URL", raising=False)
    monkeypatch.delenv("PYWEB_ENV", raising=False)
    mail.OUTBOX.clear()
    msg = mail.send("ada@example.com", "Hello", "Body text", html="<p>Body</p>")
    assert list(mail.OUTBOX) == [msg] and msg.to == ["ada@example.com"]
    built = msg.build()
    assert built["Subject"] == "Hello" and built.is_multipart()


def test_mail_refuses_header_injection():
    with pytest.raises(ValueError):
        mail.Message("ada@example.com", "Hi\r\nBcc: everyone@example.com", "x").build()


def test_production_mail_needs_a_server(monkeypatch):
    monkeypatch.delenv("PYWEB_MAIL_URL", raising=False)
    monkeypatch.setenv("PYWEB_ENV", "production")
    with pytest.raises(RuntimeError, match="PYWEB_MAIL_URL"):
        mail.send("ada@example.com", "Hi", "x")


def test_smtp_url_parsing(monkeypatch):
    monkeypatch.setenv("PYWEB_MAIL_URL", "smtps://me%40x.dev:p%3Ass@mail.x.dev")
    s = mail.sender()
    assert (s.host, s.port, s.ssl, s.user, s.password) == ("mail.x.dev", 465, True, "me@x.dev", "p:ss")
    monkeypatch.setenv("PYWEB_MAIL_URL", "smtp://mail.x.dev")
    assert mail.sender().port == 587


def test_custom_sender():
    got = []

    class Capture:
        def send(self, message):
            got.append(message)

    mail.use_sender(Capture())
    try:
        mail.send(["a@x.dev", "b@x.dev"], "Hi", "x")
    finally:
        mail.use_sender(None)
    assert got[0].to == ["a@x.dev", "b@x.dev"]
    assert mail.address("Ada, L.", "ada@x.dev") == '"Ada, L." <ada@x.dev>'


def test_argon2_when_installed_and_scrypt_hashes_move_to_it(monkeypatch):
    argon2 = pytest.importorskip("argon2")
    monkeypatch.setattr(passwords, "_argon2", lambda: argon2.PasswordHasher(time_cost=1, memory_cost=1024))
    new = passwords.hash_password("correct horse battery")
    assert new.startswith("$argon2id$") and passwords.verify_password("correct horse battery", new)
    assert not passwords.verify_password("wrong horse battery", new)
    assert not passwords.needs_rehash(new)
    monkeypatch.setattr(passwords, "_argon2", lambda: None)
    old = passwords.hash_password("correct horse battery")
    monkeypatch.setattr(passwords, "_argon2", lambda: argon2.PasswordHasher(time_cost=1, memory_cost=1024))
    assert passwords.verify_password("correct horse battery", old) and passwords.needs_rehash(old)
