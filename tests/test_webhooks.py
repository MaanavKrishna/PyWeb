"""Webhooks in (signature schemes, replay and duplicate protection, handler as a job) and out (signed,
retried, SSRF-safe, logged)."""

import hashlib
import hmac
import http.server
import json
import threading
import time

import pytest

from pyweb import jobs, webhooks
from pyweb.testing import TestClient
from pyweb.webhooks import SignatureError, verify


def test_standard_webhooks_reference_vector():
    """The example from the Standard Webhooks spec: PyWeb signs and verifies exactly like other libraries."""
    secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
    msg_id, ts, body = "msg_p5jXN8AQM9LWM0D4loKWxJek", "1614265330", b'{"test": 2432232314}'
    assert webhooks.sign_standard(secret, msg_id, ts, body) == "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="
    headers = {"webhook-id": msg_id, "webhook-timestamp": ts,
               "webhook-signature": "v1,bogus v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="}
    assert verify("standard", secret, headers, body, now=1614265330)[0] == msg_id
    with pytest.raises(SignatureError, match="stale"):
        verify("standard", secret, headers, body, now=1614265330 + 3600)
    with pytest.raises(SignatureError, match="bad signature"):
        verify("standard", secret, headers, body + b" ", now=1614265330)


def test_github_and_stripe_schemes():
    body = b'{"id": "evt_1", "type": "invoice.paid"}'
    gh = {"X-Hub-Signature-256": "sha256=" + hmac.new(b"s3", body, hashlib.sha256).hexdigest(),
          "X-GitHub-Delivery": "d-1", "X-GitHub-Event": "push"}
    assert verify("github", "s3", gh, body) == ("d-1", "push")
    with pytest.raises(SignatureError):
        verify("github", "other", gh, body)
    t = int(time.time())
    sig = hmac.new(b"sk", f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    assert verify("stripe", "sk", {"Stripe-Signature": f"t={t},v1=deadbeef,v1={sig}"}, body) == ("evt_1",
                                                                                                "invoice.paid")
    with pytest.raises(SignatureError, match="stale"):
        verify("stripe", "sk", {"Stripe-Signature": f"t={t - 1000},v1={sig}"}, body)
    with pytest.raises(SignatureError, match="no secret"):
        verify("github", "", gh, body)


APP = """
from pyweb import App, webhooks
app = App(database="sqlite:///{db}")
SEEN = []


@app.webhook("/hooks/gh", secret="s3cret", scheme="github")
def on_github(event):
    SEEN.append((event.id, event.type, event.data["ref"]))


@app.page("/")
def Home():
    <p>hi</p>
"""


@pytest.fixture
def hook_app(tmp_path, monkeypatch):
    from pyweb import models as M
    monkeypatch.setattr(M._state, "db", None)
    monkeypatch.setenv("PYWEB_WORKER", "0")
    webhooks._ready.clear()
    path = tmp_path / "app.pyweb"
    path.write_text(APP.replace("{db}", str(tmp_path / "t.db")))
    yield TestClient(str(path))
    M._state.db = None


def gh_post(client, body, delivery="d-1", secret=b"s3cret"):
    sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return client.request("POST", "/hooks/gh", body, {"Content-Type": "application/json", "X-Hub-Signature-256": sig,
                                                      "X-GitHub-Delivery": delivery, "X-GitHub-Event": "push"})


def test_receiving_runs_the_handler_once_as_a_job(hook_app):
    body = json.dumps({"ref": "refs/heads/main"}).encode()
    assert gh_post(hook_app, body).status == 202
    assert gh_post(hook_app, body).status == 200                       # the same delivery again: dropped
    assert gh_post(hook_app, body, secret=b"wrong").status == 401
    assert hook_app.request("GET", "/hooks/gh").status == 405
    assert jobs.Worker(concurrency=1, schedule=False).drain(timeout=10) == 1
    assert hook_app.site.app.module.SEEN == [("d-1", "push", "refs/heads/main")]


@pytest.fixture
def endpoint():
    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.append((dict(self.headers), body))
            code = 500 if len(got) == 1 and self.path == "/flaky" else 200
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.got = got
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()


def test_sending_signs_retries_and_logs(hook_app, endpoint, monkeypatch):
    monkeypatch.setenv("PYWEB_FETCH_ALLOW", "127.0.0.0/8")
    monkeypatch.setattr(jobs.core, "backoff_delay", lambda *a, **k: 0)
    from pyweb.models import system
    secret = webhooks.new_secret()
    row = webhooks.send(endpoint.url + "/flaky", "invoice.paid", {"id": 42}, secret=secret)
    for _ in range(3):
        jobs.Worker(concurrency=1, schedule=False).drain(timeout=10)
    assert len(endpoint.got) == 2                                      # failed once, then delivered
    headers, body = endpoint.got[-1]
    lower = {k.lower(): v for k, v in headers.items()}
    assert verify("standard", secret, lower, body)[0] == row.message_id
    assert json.loads(body)["data"] == {"id": 42} and json.loads(body)["type"] == "invoice.paid"
    with system():
        done = webhooks.WebhookDelivery.get(row.id)
    assert done.status == "delivered" and done.attempts == 2 and done.response_status == 200


def test_endpoints_cant_point_at_the_internal_network(hook_app, monkeypatch):
    monkeypatch.delenv("PYWEB_FETCH_ALLOW", raising=False)
    # Retries use "full jitter" (0 to 4 s for the first): pin it, so drain() can't also run the retry.
    monkeypatch.setattr(jobs.core, "backoff_delay", lambda *a, **k: 60.0)
    from pyweb.models import system
    row = webhooks.send("http://169.254.169.254/latest", "x", {}, secret="whsec_" + "A" * 44)
    jobs.Worker(concurrency=1, schedule=False).drain(timeout=10)
    with system():
        failed = webhooks.WebhookDelivery.get(row.id)
    assert failed.status == "pending" and "public address" in failed.error and failed.attempts == 1


def test_publish_goes_to_the_organizations_subscribed_endpoints(hook_app, endpoint, monkeypatch):
    monkeypatch.setenv("PYWEB_FETCH_ALLOW", "127.0.0.0/8")
    from pyweb.models import system
    from pyweb.tenancy import org_scope
    webhooks.ensure_tables()
    with system():
        webhooks.WebhookEndpoint.create(org_id=1, url=endpoint.url + "/a", events=["invoice.paid"])
        webhooks.WebhookEndpoint.create(org_id=1, url=endpoint.url + "/b", events=["user.created"])
        webhooks.WebhookEndpoint.create(org_id=2, url=endpoint.url + "/c")
    with org_scope(1):
        sent = webhooks.publish("invoice.paid", {"n": 1})
    assert [d.url.rsplit("/", 1)[1] for d in sent] == ["a"]
    jobs.Worker(concurrency=1, schedule=False).drain(timeout=10)
    assert len(endpoint.got) == 1
