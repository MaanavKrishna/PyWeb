"""Web Push: VAPID keys and JWTs, RFC 8291 encryption, subscriptions, delivery through a mock push service."""

import base64
import http.server
import json
import threading

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402

from pyweb import jobs, push  # noqa: E402
from pyweb.push import b64d, b64e  # noqa: E402
from pyweb.testing import TestClient  # noqa: E402


def browser_keys():
    """What a browser makes when it subscribes: a P-256 key pair and a 16-byte auth secret."""
    key = ec.generate_private_key(ec.SECP256R1())
    auth = b64e(b"0123456789abcdef")
    return key, b64e(push._point(key.public_key())), auth


def test_rfc8291_example():
    """The worked example in RFC 8291 section 5: same keys and salt, byte-identical message."""
    from cryptography.hazmat.primitives.asymmetric import ec as _ec
    as_private = _ec.derive_private_key(int.from_bytes(b64d("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
                                        _ec.SECP256R1())
    body = push.encrypt(
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "BTBZMqHH6r4Tts7J_aSIgg", b"When I grow up, I want to be a watermelon",
        salt=b64d("DGv6ra1nlYgDCS1FRnbzlw"), server_key=as_private)
    assert b64e(body) == ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6Tlz"
                          "AC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-"
                          "fN")
    ua_private = _ec.derive_private_key(int.from_bytes(b64d("q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"), "big"),
                                        _ec.SECP256R1())
    assert push.decrypt(body, ua_private, "BTBZMqHH6r4Tts7J_aSIgg") == b"When I grow up, I want to be a watermelon"


def test_encrypt_round_trip_and_size_limit():
    key, p256dh, auth = browser_keys()
    msg = "héllo ".encode() * 10
    body = push.encrypt(p256dh, auth, msg)
    assert body[16:20] == (4096).to_bytes(4, "big") and body[20] == 65
    assert push.decrypt(body, key, auth) == msg
    assert push.encrypt(p256dh, auth, msg)[:16] != body[:16]            # a fresh salt and key every time
    with pytest.raises(push.PushError, match="at most"):
        push.encrypt(p256dh, auth, b"x" * (push.MAX_PAYLOAD + 1))


def _verify_jwt(header):
    scheme, _, rest = header.partition(" ")
    assert scheme == "vapid"
    parts = dict(p.strip().split("=", 1) for p in rest.split(","))
    jwt, k = parts["t"], parts["k"]
    head, claims, sig = jwt.split(".")
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64d(k))
    raw = b64d(sig)
    pub.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
               f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    return json.loads(b64d(head)), json.loads(b64d(claims)), k


def test_vapid_keys_and_jwt(monkeypatch):
    monkeypatch.delenv("PYWEB_VAPID_KEY", raising=False)
    monkeypatch.setenv("PYWEB_AUTH_SECRET", "s" * 40)
    derived = push.public_key()
    assert len(b64d(derived)) == 65 and push.public_key() == derived   # stable for the same secret
    monkeypatch.setenv("PYWEB_AUTH_SECRET", "t" * 40)
    assert push.public_key() != derived
    key = push.new_key()
    monkeypatch.setenv("PYWEB_VAPID_KEY", key)
    pinned = push.public_key()
    monkeypatch.setenv("PYWEB_AUTH_SECRET", "u" * 40)
    assert push.public_key() == pinned                                 # PYWEB_VAPID_KEY survives secret rotation
    monkeypatch.setenv("PYWEB_PUSH_CONTACT", "ops@example.com")
    head, claims, k = _verify_jwt(push.vapid_header("https://fcm.googleapis.com/fcm/send/abc", now=1000))
    assert head == {"typ": "JWT", "alg": "ES256"} and k == pinned
    assert claims == {"aud": "https://fcm.googleapis.com", "exp": 1000 + 12 * 3600, "sub": "mailto:ops@example.com"}
    monkeypatch.setenv("PYWEB_VAPID_KEY", "not-a-key!")
    with pytest.raises(push.PushError):
        push.public_key()


def test_production_needs_a_key(monkeypatch):
    monkeypatch.delenv("PYWEB_VAPID_KEY", raising=False)
    monkeypatch.delenv("PYWEB_AUTH_SECRET", raising=False)
    monkeypatch.setenv("PYWEB_ENV", "production")
    with pytest.raises(push.PushError, match="PYWEB_VAPID_KEY"):
        push.public_key()


def test_only_real_push_services(monkeypatch):
    monkeypatch.delenv("PYWEB_PUSH_HOSTS", raising=False)
    ok = ["https://fcm.googleapis.com/fcm/send/x", "https://updates.push.services.mozilla.com/wpush/v2/x",
          "https://web.push.apple.com/x", "https://wns2-par02p.notify.windows.com/w/?token=x"]
    bad = ["http://fcm.googleapis.com/x", "https://evil.example/x", "https://fcm.googleapis.com.evil.example/x",
           "https://169.254.169.254/latest", "javascript:alert(1)", "https://notify.windows.com.evil/x", ""]
    assert all(push.allowed_endpoint(u) for u in ok)
    assert not any(push.allowed_endpoint(u) for u in bad)
    monkeypatch.setenv("PYWEB_PUSH_HOSTS", "push.example.org,127.0.0.1")
    assert push.allowed_endpoint("https://push.example.org/x") and push.allowed_endpoint("http://127.0.0.1:9/x")
    assert push.allowed_endpoint("http://push.example.org/x")          # plain http only for hosts named there
    assert not push.allowed_endpoint("http://fcm.googleapis.com/x")


def test_message_rules():
    assert json.loads(push.message("Hi", "there", url="/inbox", tag="t")) == {
        "title": "Hi", "body": "there", "url": "/inbox", "tag": "t"}
    for url in ("https://evil.example/", "//evil.example", "javascript:alert(1)"):
        with pytest.raises(ValueError, match="path in this app"):
            push.message("Hi", url=url)
    with pytest.raises(push.PushError, match="too long"):
        push.message("x" * 5000)
    with pytest.raises(ValueError, match="urgency"):
        push.send([], "x", urgency="now")


APP = """
from pyweb import App, server, session
app = App(database="sqlite:///{db}", pwa=True)


@server
def sign_in(user_id: int) -> bool:
    session.login(user_id)
    return True


@app.page("/")
def Home():
    <button data-pw-push="subscribe">Notify me</button>
"""


@pytest.fixture
def push_app(tmp_path, monkeypatch):
    from pyweb import models as M
    monkeypatch.setattr(M._state, "db", None)
    monkeypatch.setenv("PYWEB_WORKER", "0")
    monkeypatch.setenv("PYWEB_AUTH_SECRET", "p" * 40)
    monkeypatch.delenv("PYWEB_VAPID_KEY", raising=False)
    push._ready.clear()
    path = tmp_path / "app.pyweb"
    path.write_text(APP.replace("{db}", str(tmp_path / "t.db")))
    yield TestClient(str(path))
    M._state.db = None


@pytest.fixture
def push_service(monkeypatch):
    """A push service on localhost: records requests, answers with the status set for each path."""
    got, answers = [], {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.append((self.path, dict(self.headers), body))
            codes = answers.get(self.path, [201])
            code = codes.pop(0) if len(codes) > 1 else codes[0]
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("PYWEB_PUSH_HOSTS", "127.0.0.1")
    monkeypatch.setattr(jobs.core, "backoff_delay", lambda *a, **k: 0)
    srv.got, srv.answers = got, answers
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()


def subscribe(client, endpoint, p256dh, auth, **headers):
    return client.post("/__pyweb/push/subscribe", {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
                       headers=headers)


def drain(times=1):
    for _ in range(times):
        jobs.Worker(concurrency=1, schedule=False).drain(timeout=10)


def test_subscribe_send_and_receive(push_app, push_service):
    key = push_app.get("/__pyweb/push/key").json()["key"]
    assert key == push.public_key()
    assert push_app.rpc("sign_in", user_id=7) is True
    bkey, p256dh, auth = browser_keys()
    r = subscribe(push_app, push_service.url + "/sub/1", p256dh, auth)
    assert r.status == 200
    [row] = push.subscriptions(7)
    assert row.endpoint == push_service.url + "/sub/1"
    # Subscribing again updates the row instead of adding one.
    assert subscribe(push_app, push_service.url + "/sub/1", p256dh, auth).status == 200
    assert len(push.subscriptions()) == 1

    assert push.send(7, "Export ready", "Tap to download", url="/exports", tag="export", urgency="high") == 1
    drain()
    [(path, headers, body)] = push_service.got
    h = {k.lower(): v for k, v in headers.items()}
    assert path == "/sub/1" and h["content-encoding"] == "aes128gcm" and h["ttl"] == "86400"
    assert h["urgency"] == "high"
    _, claims, k = _verify_jwt(h["authorization"])
    assert k == key and claims["aud"] == push_service.url
    assert json.loads(push.decrypt(body, bkey, auth)) == {
        "title": "Export ready", "body": "Tap to download", "url": "/exports", "tag": "export"}
    assert push.subscriptions(7)[0].last_sent_at is not None


def test_gone_subscriptions_are_deleted_and_busy_services_retried(push_app, push_service):
    _, p256dh, auth = browser_keys()
    push.subscribe(push_service.url + "/gone", p256dh, auth, user=1)
    push.subscribe(push_service.url + "/busy", p256dh, auth, user=1)
    push_service.answers["/gone"] = [410]
    push_service.answers["/busy"] = [503, 201]
    assert push.send(1, "hi") == 2
    drain(3)
    paths = [p for p, _, _ in push_service.got]
    assert paths.count("/gone") == 1 and paths.count("/busy") == 2
    assert [s.endpoint.rsplit("/", 1)[1] for s in push.subscriptions(1)] == ["busy"]


def test_subscription_requests_are_checked(push_app, push_service):
    _, p256dh, auth = browser_keys()
    ep = push_service.url + "/s"
    assert push_app.get("/__pyweb/push/subscribe").status == 405
    assert push_app.request("POST", "/__pyweb/push/subscribe", b"endpoint=x",
                            {"Content-Type": "application/x-www-form-urlencoded"}).status == 415
    assert subscribe(push_app, ep, p256dh, auth, **{"Sec-Fetch-Site": "cross-site"}).status == 403
    assert subscribe(push_app, ep, p256dh, auth, Origin="https://evil.example").status == 403
    assert subscribe(push_app, "https://evil.example/collect", p256dh, auth).status == 422
    assert subscribe(push_app, ep, p256dh[:-4], auth).status == 422
    assert subscribe(push_app, ep, p256dh, b64e(b"short")).status == 422
    assert push_app.request("POST", "/__pyweb/push/subscribe", b"[1]", {"Content-Type": "application/json"}).status == 400
    assert push.subscriptions() == []
    # Signed out is fine (the subscription has no user); unsubscribe deletes it.
    assert subscribe(push_app, ep, p256dh, auth).status == 200
    assert push.subscriptions()[0].user_id is None
    r = push_app.post("/__pyweb/push/unsubscribe", {"endpoint": ep})         # without its keys: kept
    assert r.status == 200 and r.json()["deleted"] is False and len(push.subscriptions()) == 1
    r = push_app.post("/__pyweb/push/unsubscribe", {"endpoint": ep, "keys": {"auth": auth}})
    assert r.status == 200 and r.json()["deleted"] is True and push.subscriptions() == []


def test_someone_who_knows_an_endpoint_cant_take_it_over(push_app, push_service):
    _, p256dh, auth = browser_keys()
    _, other_key, _ = browser_keys()
    other_auth = b64e(b"fedcba9876543210")
    ep = push_service.url + "/mine"
    push.subscribe(ep, p256dh, auth, user=7)
    assert push_app.rpc("sign_in", user_id=8) is True
    assert subscribe(push_app, ep, other_key, other_auth).status == 422          # someone else's browser
    assert push.subscriptions(7) and not push.subscriptions(8)
    assert subscribe(push_app, ep, p256dh, auth).status == 200                   # the same browser, new user
    assert push.subscriptions(8)


def test_broadcast(push_app, push_service):
    _, p256dh, auth = browser_keys()
    for i in range(3):
        push.subscribe(f"{push_service.url}/b{i}", p256dh, auth, user=i)
    assert push.broadcast("Maintenance at 2am") == 3
    drain()
    assert sorted(p for p, _, _ in push_service.got) == ["/b0", "/b1", "/b2"]


def test_push_endpoints_need_pwa(tmp_path):
    c = TestClient(source='from pyweb import App\napp = App()\n\n\n@app.page("/")\ndef Home():\n    <p>x</p>\n')
    assert c.get("/__pyweb/push/key").status == 404


def test_cli_makes_a_key(capsys):
    from pyweb.cli import main
    main(["push", "key"])
    out = capsys.readouterr().out
    key = next(line.split("=", 1)[1] for line in out.splitlines() if line.startswith("PYWEB_VAPID_KEY="))
    assert len(base64.urlsafe_b64decode(key + "=")) == 32


def test_production_check(monkeypatch):
    from pyweb.cli import production_problems
    monkeypatch.delenv("PYWEB_VAPID_KEY", raising=False)
    monkeypatch.delenv("PYWEB_PUSH_CONTACT", raising=False)
    src = "app = App(pwa=True)\n"
    found = production_problems(src)
    assert any("PYWEB_VAPID_KEY isn't set" in p for p in found) and any("PYWEB_PUSH_CONTACT" in p for p in found)
    assert not any("VAPID" in p for p in production_problems("app = App(pwa=PWA(push=False))\n"))
    monkeypatch.setenv("PYWEB_VAPID_KEY", push.new_key())
    monkeypatch.setenv("PYWEB_PUSH_CONTACT", "ops@example.com")
    assert not any("VAPID" in p or "PUSH_CONTACT" in p for p in production_problems(src))
