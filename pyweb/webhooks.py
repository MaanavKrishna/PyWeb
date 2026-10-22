"""Webhooks: receive them from other services, and send your own.

Receiving::

    @app.webhook("/hooks/github", secret=os.environ["GITHUB_WEBHOOK_SECRET"], scheme="github")
    def on_github(event):
        if event.type == "push":
            ...

PyWeb checks the signature (constant time) and, for schemes that sign a
timestamp, that it's recent (5 minutes); refuses a delivery it has seen
before (by the event's id, so a retried or replayed delivery runs once);
answers ``202`` at once; and runs the handler as a background job, so a
failure is retried with backoff. Schemes: ``"github"`` (``X-Hub-Signature-256``),
``"stripe"`` (``Stripe-Signature``) and ``"standard"`` (the Standard Webhooks
spec: ``webhook-id``, ``webhook-timestamp``, ``webhook-signature``, used by
Svix, Clerk, Resend and others).

Sending::

    from pyweb import webhooks
    webhooks.send("https://example.com/hooks", "invoice.paid", {"id": 42}, secret=endpoint_secret)

Deliveries are signed with the Standard Webhooks scheme, queued as jobs,
retried with backoff (8 attempts over about a day), go through
:func:`pyweb.http.fetch` (so an endpoint can't point at your internal
network) and are logged in ``pyweb_webhook_deliveries``. With organizations,
:class:`WebhookEndpoint` rows hold each organization's endpoints and
``webhooks.publish("invoice.paid", data)`` sends to every endpoint of the
current organization that subscribed to the event.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import time

from pyweb.models import Field, Index, Model

TOLERANCE = 300                     # seconds a signed timestamp may be off
SCHEMES = ("github", "stripe", "standard")


class SignatureError(Exception):
    pass


# ------------------------------------------------------------------ tables (PyWeb's own)

class WebhookEvent(Model):
    """A received delivery, so a repeat of the same event id runs once."""

    source: str = Field(max=200)
    event_id: str = Field(max=200)
    event_type: str = Field("", max=200)
    received_at: dt.datetime = Field(auto_now_add=True)

    class Meta:
        table = "pyweb_webhook_events"
        indexes = [Index("source", "event_id", unique=True)]


class WebhookDelivery(Model):
    """One webhook PyWeb sent (or is still trying to send)."""

    org_id: int | None = None
    url: str = Field(max=2000)
    event_type: str = Field(max=200)
    message_id: str = Field(max=64)
    status: str = Field("pending", max=20)           # pending, delivered, failed
    attempts: int = 0
    response_status: int | None = None
    error: str | None = Field(None, max=500)
    created_at: dt.datetime = Field(auto_now_add=True)
    delivered_at: dt.datetime | None = None

    class Meta:
        table = "pyweb_webhook_deliveries"
        indexes = [Index("org_id", "created_at")]


class WebhookEndpoint(Model):
    """Where an organization (or the whole app, ``org_id=None``) wants events sent."""

    org_id: int | None = None
    url: str = Field(max=2000, format="url")
    secret: str = Field(default_factory=lambda: new_secret(), max=100, private=True)
    events: list = Field(default_factory=lambda: ["*"])
    active: bool = True
    created_at: dt.datetime = Field(auto_now_add=True)

    class Meta:
        table = "pyweb_webhook_endpoints"

    def wants(self, event_type):
        return self.active and ("*" in (self.events or []) or event_type in (self.events or []))


MODELS = [WebhookEvent, WebhookDelivery, WebhookEndpoint]
_ready: set = set()


def ensure_tables(db=None):
    """Create PyWeb's webhook tables in ``db`` if missing (they're PyWeb's own, like the job queue's)."""
    from pyweb import models as M
    db = db or M.database() or WebhookEvent._database()
    if id(db) not in _ready:
        M.ensure_tables(db, MODELS)
        _ready.add(id(db))
    return db


def new_secret():
    """A Standard Webhooks secret: ``whsec_`` and 32 random bytes in base64."""
    return "whsec_" + base64.b64encode(secrets.token_bytes(32)).decode()


# ------------------------------------------------------------------ signatures

def _header(headers, name):
    name = name.lower()
    return next((v for k, v in (headers or {}).items() if k.lower() == name), "") or ""


def _standard_key(secret):
    if secret.startswith("whsec_"):
        return base64.b64decode(secret[len("whsec_"):])
    return secret.encode()


def sign_standard(secret, msg_id, timestamp, body: bytes):
    """The Standard Webhooks ``webhook-signature`` value for a message."""
    signed = f"{msg_id}.{timestamp}.".encode() + body
    mac = hmac.new(_standard_key(secret), signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def verify(scheme, secret, headers, body: bytes, *, now=None):
    """``(event_id, event_type)`` for a correctly signed delivery; :class:`SignatureError` otherwise."""
    now = time.time() if now is None else now
    if not secret:
        raise SignatureError("no secret configured for this webhook")
    if scheme == "github":
        sent = _header(headers, "X-Hub-Signature-256")
        good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not sent or not hmac.compare_digest(sent, good):
            raise SignatureError("bad signature")
        return _header(headers, "X-GitHub-Delivery"), _header(headers, "X-GitHub-Event")
    if scheme == "stripe":
        parts = {}
        for item in _header(headers, "Stripe-Signature").split(","):
            k, _, v = item.strip().partition("=")
            parts.setdefault(k, []).append(v)
        ts = (parts.get("t") or [""])[0]
        if not ts.isdigit() or abs(now - int(ts)) > TOLERANCE:
            raise SignatureError("missing or stale timestamp")
        good = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(s, good) for s in parts.get("v1", [])):
            raise SignatureError("bad signature")
        data = _json(body)
        return str(data.get("id", "")), str(data.get("type", ""))
    if scheme == "standard":
        msg_id = _header(headers, "webhook-id")
        ts = _header(headers, "webhook-timestamp")
        if not msg_id or not ts.isdigit() or abs(now - int(ts)) > TOLERANCE:
            raise SignatureError("missing id or stale timestamp")
        good = sign_standard(secret, msg_id, ts, body).split(",", 1)[1]
        sent = [s.split(",", 1)[1] for s in _header(headers, "webhook-signature").split() if s.startswith("v1,")]
        if not any(hmac.compare_digest(s, good) for s in sent):
            raise SignatureError("bad signature")
        data = _json(body)
        return msg_id, str(data.get("type", ""))
    raise SignatureError(f"unknown scheme {scheme!r}")


def _json(body):
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {"data": data}


# ------------------------------------------------------------------ receiving

@dataclasses.dataclass
class Event:
    """A received webhook, as the handler sees it."""
    id: str
    type: str
    data: dict
    headers: dict
    source: str


@dataclasses.dataclass
class Receiver:
    path: str
    fn: object
    secret: object               # a string, or a function returning it (read when a delivery arrives)
    scheme: str
    job: object = None

    def secret_value(self):
        return self.secret() if callable(self.secret) else self.secret


KEEP_HEADERS = ("content-type", "user-agent", "x-github-event", "x-github-delivery", "webhook-id",
                "webhook-timestamp")


def receiver(path, fn, *, secret, scheme="standard", retries=5):
    """A :class:`Receiver` whose handler runs as a durable job."""
    if scheme not in SCHEMES:
        raise ValueError(f"scheme must be one of {', '.join(SCHEMES)}")
    from pyweb import jobs

    def run(event):
        return fn(Event(**event))
    run.__name__ = getattr(fn, "__name__", "webhook")
    job = jobs.job(run, name=f"webhook:{path}", retries=retries)
    return Receiver(path, fn, secret, scheme, job)


def receive(rcv, req):
    """Answer a delivery to ``rcv.path``: 202 (queued), 200 (seen before), 401 (bad signature), 405."""
    from pyweb.runtime.server import Response

    def answer(status, body):
        return Response(status, json.dumps(body), {"Content-Type": "application/json", "Cache-Control": "no-store"})
    if req.method != "POST":
        return answer(405, {"error": "POST only"})
    body = req.body or b""
    try:
        event_id, event_type = verify(rcv.scheme, rcv.secret_value(), req.headers, body)
    except SignatureError as exc:
        return answer(401, {"error": str(exc)})
    data = _json(body)
    event_id = event_id or hashlib.sha256(body).hexdigest()[:40]    # unsigned ids: the body itself
    from pyweb.models import system
    ensure_tables()
    event_id = event_id[:200]
    with system():
        seen = WebhookEvent.where(source=rcv.path, event_id=event_id).exists()
        if not seen:
            try:
                WebhookEvent.create(source=rcv.path, event_id=event_id, event_type=event_type[:200])
            except Exception:  # noqa: BLE001 - the same event arriving twice at once: the unique index wins
                seen = True
    if seen:
        return answer(200, {"ok": True, "duplicate": True})
    headers = {k.lower(): v for k, v in (req.headers or {}).items() if k.lower() in KEEP_HEADERS}
    rcv.job.enqueue({"id": event_id, "type": event_type, "data": data, "headers": headers, "source": rcv.path},
                    key=f"webhook:{rcv.path}:{event_id}")
    return answer(202, {"ok": True})


# ------------------------------------------------------------------ sending

def _deliver(delivery_id, url, secret, msg_id, event_type, payload):
    """The job behind :func:`send`: one signed POST; raises (so the job retries) unless it's a 2xx."""
    from pyweb.http import FetchError, fetch
    from pyweb.models import system
    body = json.dumps({"type": event_type, "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                       "data": payload}, separators=(",", ":"), default=str).encode()
    ts = str(int(time.time()))
    headers = {"Content-Type": "application/json", "webhook-id": msg_id, "webhook-timestamp": ts,
               "webhook-signature": sign_standard(secret, msg_id, ts, body), "User-Agent": "PyWeb-Webhooks"}
    with system():
        row = WebhookDelivery.get(delivery_id)
        attempts = (row.attempts if row else 0) + 1
    try:
        resp = fetch(url, method="POST", data=body, headers=headers, timeout=10, max_bytes=64_000)
        status, error = resp.status, None if resp.ok else f"HTTP {resp.status}"
    except FetchError as exc:
        status, error = None, str(exc)[:500]
    # Each attempt is its own job and always succeeds, so its log entry commits; a failed attempt
    # queues the next one itself, later each time.
    with system():
        WebhookDelivery.where(id=delivery_id).update(
            attempts=attempts, response_status=status, error=error,
            status="delivered" if error is None else ("failed" if attempts >= ATTEMPTS else "pending"),
            delivered_at=dt.datetime.now(dt.timezone.utc) if error is None else None)
    if error is not None and attempts < ATTEMPTS:
        from pyweb.jobs import core
        _register().enqueue(delivery_id, url, secret, msg_id, event_type, payload,
                            delay=core.backoff_delay(attempts, base=4.0, cap=6 * 3600))
    return {"status": status, "error": error, "attempt": attempts}


ATTEMPTS = 8


def _register():
    from pyweb import jobs
    if "pyweb.webhooks.deliver" not in jobs.REGISTRY:
        jobs.job(_deliver, name="pyweb.webhooks.deliver", retries=2)
    return jobs.REGISTRY["pyweb.webhooks.deliver"]


def send(url, event_type, data, *, secret, org=None):
    """Queue a signed webhook to ``url``; returns the :class:`WebhookDelivery` row."""
    from pyweb.models import system
    ensure_tables()
    msg_id = "msg_" + secrets.token_hex(12)
    with system():
        row = WebhookDelivery.create(url=url, event_type=event_type, message_id=msg_id,
                                     org_id=getattr(org, "id", org))
    _register().enqueue(row.id, url, secret, msg_id, event_type, data)
    return row


def publish(event_type, data, org=None):
    """Send ``event_type`` to every active endpoint of ``org`` (default: the current organization,
    or the app-wide endpoints outside organizations) that subscribed to it. Returns the deliveries."""
    from pyweb import tenancy
    from pyweb.models import system
    org_id = getattr(org, "id", org) if org is not None else tenancy.current_org_id()
    ensure_tables()
    with system():
        endpoints = [e for e in WebhookEndpoint.where(org_id=org_id) if e.wants(event_type)]
    return [send(e.url, event_type, data, secret=e.secret, org=org_id) for e in endpoints]


def env_secret(name):
    """``secret=env_secret("GITHUB_WEBHOOK_SECRET")``: read the secret when a delivery arrives."""
    return lambda: os.environ.get(name, "")
