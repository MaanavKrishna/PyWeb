"""Web Push notifications for installable apps (``App(pwa=True)``).

In the page, a button asks for permission (browsers only allow asking on a click)::

    <button data-pw-push="subscribe">Turn on notifications</button>

On the server, from a server function, a job or a script::

    from pyweb import push
    push.send(user, "Your export is ready", "Tap to download it", url="/exports")

Each notification is encrypted for the receiving browser (RFC 8291, ``aes128gcm``),
signed with the app's VAPID key (RFC 8292) and delivered by a background job through
:func:`pyweb.http.fetch`. Subscriptions the push service says are gone (404/410) are
deleted. Needs ``pip install "pyweb-stack[push]"`` (the ``cryptography`` package).

Keys: the VAPID key comes from ``PYWEB_VAPID_KEY`` (make one with ``pyweb push key``), or
is derived from ``PYWEB_AUTH_SECRET``. Set ``PYWEB_VAPID_KEY`` in production: browsers
tie each subscription to the key, so changing it (or rotating the auth secret without it)
means every visitor has to turn notifications on again. ``PYWEB_PUSH_CONTACT`` (a
``mailto:`` or ``https:`` address) tells push services who to contact about problems.

Subscriptions are only accepted for the push services browsers use (Google, Mozilla,
Apple, Microsoft); ``PYWEB_PUSH_HOSTS`` adds others (comma-separated host names). So a
visitor can't make your server send requests anywhere else.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import struct
import time
from urllib.parse import urlparse

from pyweb.models import Field, Model

log = logging.getLogger("pyweb.push")

PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "push.services.mozilla.com",
              "push.apple.com", "notify.windows.com")
MAX_PAYLOAD = 3993             # 4096-byte record, minus the header (86), the tag (16) and the delimiter (1)
RECORD_SIZE = 4096
URGENCY = ("very-low", "low", "normal", "high")


class PushError(Exception):
    pass


class PushSubscription(Model):
    """A browser that agreed to receive notifications (PyWeb's own table)."""

    endpoint_hash: str = Field(unique=True, max=64)
    endpoint: str = Field()
    p256dh: str = Field(max=200)
    auth: str = Field(max=50)
    user_id: int | None = Field(None, index=True)
    org_id: int | None = None
    user_agent: str | None = Field(None, max=300)
    created_at: dt.datetime = Field(auto_now_add=True)
    last_sent_at: dt.datetime | None = None
    failures: int = 0

    class Meta:
        table = "pyweb_push_subscriptions"


_ready: set = set()


def ensure_tables(db=None):
    from pyweb import models as M
    db = db or M.database() or PushSubscription._database()
    if id(db) not in _ready:
        M.ensure_tables(db, [PushSubscription])
        _ready.add(id(db))
    return db


# ------------------------------------------------------------------ helpers

def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64d(text: str) -> bytes:
    text = (text or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _crypto():
    try:
        from cryptography.hazmat.primitives import hashes, serialization  # noqa: F401
        from cryptography.hazmat.primitives.asymmetric import ec  # noqa: F401
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    except ImportError:
        raise PushError('Web Push needs the cryptography package: pip install "pyweb-stack[push]"') from None


def _hmac(key, data):
    import hmac
    return hmac.new(key, data, hashlib.sha256).digest()


# ------------------------------------------------------------------ VAPID (RFC 8292)

_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551   # order of P-256
_key_cache: dict = {}


def _root_secret():
    secret = os.environ.get("PYWEB_AUTH_SECRET")
    if secret:
        return secret
    if os.environ.get("PYWEB_ENV", "development") == "production":
        raise PushError("set PYWEB_VAPID_KEY (or PYWEB_AUTH_SECRET) to send push notifications in production")
    from pyweb.context import _dev_secret
    return _dev_secret


def private_number() -> int:
    """The VAPID private key as a number: ``PYWEB_VAPID_KEY``, or derived from the auth secret."""
    raw = os.environ.get("PYWEB_VAPID_KEY", "").strip()
    if raw:
        try:
            value = int.from_bytes(b64d(raw), "big")
        except ValueError:
            raise PushError("PYWEB_VAPID_KEY isn't a key made by `pyweb push key`") from None
        if not 0 < value < _N:
            raise PushError("PYWEB_VAPID_KEY isn't a valid P-256 private key")
        return value
    from pyweb import keys
    return int(keys.derive(_root_secret(), "vapid"), 16) % (_N - 1) + 1


def new_key() -> str:
    """A new random VAPID private key, for ``PYWEB_VAPID_KEY``."""
    import secrets
    return b64e((secrets.randbelow(_N - 1) + 1).to_bytes(32, "big"))


def _private_key():
    _crypto()
    from cryptography.hazmat.primitives.asymmetric import ec
    d = private_number()
    if d not in _key_cache:
        _key_cache.clear()
        _key_cache[d] = ec.derive_private_key(d, ec.SECP256R1())
    return _key_cache[d]


def _point(public_key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def public_key() -> str:
    """The VAPID public key (base64url, uncompressed point): what browsers subscribe with."""
    return b64e(_point(_private_key().public_key()))


def contact():
    value = os.environ.get("PYWEB_PUSH_CONTACT", "").strip()
    if value:
        return value if value.startswith(("mailto:", "https:")) else "mailto:" + value
    return "mailto:push@example.invalid"


def vapid_header(endpoint, *, now=None, ttl=12 * 3600) -> str:
    """``Authorization`` header value for a request to ``endpoint``: ``vapid t=<JWT>, k=<public key>``."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    u = urlparse(endpoint)
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int((now or time.time()) + ttl), "sub": contact()}
    signing_input = (b64e(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode()) + "."
                     + b64e(json.dumps(claims, separators=(",", ":")).encode()))
    key = _private_key()
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    jwt = signing_input + "." + b64e(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={jwt}, k={b64e(_point(key.public_key()))}"


# ------------------------------------------------------------------ encryption (RFC 8291 / 8188)

def encrypt(p256dh: str, auth: str, plaintext: bytes, *, salt: bytes | None = None, server_key=None) -> bytes:
    """``plaintext`` encrypted for one browser (``aes128gcm`` content coding): the request body."""
    _crypto()
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(plaintext) > MAX_PAYLOAD:
        raise PushError(f"push messages can be at most {MAX_PAYLOAD} bytes (this one is {len(plaintext)})")
    ua_public = b64d(p256dh)
    auth_secret = b64d(auth)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _point(server_key.public_key())
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = server_key.exchange(ec.ECDH(), ua_key)
    salt = salt or os.urandom(16)
    # IKM = HKDF(auth_secret, ecdh_secret, "WebPush: info" || 0 || ua_public || as_public, 32)
    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    record = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)     # one record, the last one
    return salt + struct.pack(">IB", RECORD_SIZE, len(as_public)) + as_public + record


def decrypt(body: bytes, private_key, auth: str) -> bytes:
    """The browser's side of :func:`encrypt` (used by tests and the mock push service)."""
    _crypto()
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, (_rs, idlen) = body[:16], struct.unpack(">IB", body[16:21])
    as_public = body[21:21 + idlen]
    record = body[21 + idlen:]
    ua_public = _point(private_key.public_key())
    shared = private_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    prk_key = _hmac(b64d(auth), shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    plain = AESGCM(cek).decrypt(nonce, record, None)
    return plain.rstrip(b"\x00")[:-1]


# ------------------------------------------------------------------ subscriptions

def allowed_endpoint(endpoint) -> bool:
    """True for an https URL of a known push service (or one listed in ``PYWEB_PUSH_HOSTS``)."""
    try:
        u = urlparse(endpoint or "")
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    extra = [h.strip().lower() for h in os.environ.get("PYWEB_PUSH_HOSTS", "").split(",") if h.strip()]
    if u.scheme == "http" and host in extra:
        return True                     # a local push service named on purpose (tests, development)
    if u.scheme != "https" or not host or len(endpoint) > 2000:
        return False
    return any(host == h or host.endswith("." + h) for h in (*PUSH_HOSTS, *extra))


def _hash(endpoint):
    return hashlib.sha256(endpoint.encode()).hexdigest()


def subscribe(endpoint, p256dh, auth, *, user=None, org=None, user_agent=None):
    """Store (or update) a browser's subscription; returns the row. Raises ValueError on bad input."""
    from pyweb.models import system
    if not allowed_endpoint(endpoint):
        raise ValueError("this push service isn't allowed (see PYWEB_PUSH_HOSTS)")
    try:
        key, secret = b64d(p256dh), b64d(auth)
    except ValueError:
        raise ValueError("subscription keys aren't base64url") from None
    if len(key) != 65 or key[0] != 4 or len(secret) != 16:
        raise ValueError("subscription keys have the wrong size")
    ensure_tables()
    user_id = getattr(user, "id", user)
    org_id = getattr(org, "id", org)
    with system():
        row = PushSubscription.where(endpoint_hash=_hash(endpoint)).first()
        if row is not None and row.user_id is not None and row.user_id != user_id \
                and not hmac.compare_digest(str(row.auth or ""), str(auth or "")):
            # Someone else's browser: only that browser (it holds the auth secret) may re-register it.
            raise ValueError("this subscription belongs to another browser")
        if row is not None:
            PushSubscription.where(id=row.id).update(p256dh=p256dh, auth=auth, user_id=user_id, org_id=org_id,
                                                     failures=0, user_agent=user_agent)
            return PushSubscription.get(row.id)
        return PushSubscription.create(endpoint_hash=_hash(endpoint), endpoint=endpoint, p256dh=p256dh, auth=auth,
                                       user_id=user_id, org_id=org_id, user_agent=user_agent)


def unsubscribe(endpoint, *, auth=None, user=None) -> bool:
    """Delete a subscription. From a request, pass what proves it's yours: the browser's ``auth`` key
    or the signed-in ``user`` who owns it (without either, any subscription goes: server code only)."""
    from pyweb.models import system
    ensure_tables()
    with system():
        q = PushSubscription.where(endpoint_hash=_hash(endpoint or ""))
        if auth is not None or user is not None:
            row = q.first()
            if row is None:
                return False
            mine = (user is not None and row.user_id == getattr(user, "id", user)) or \
                (auth is not None and hmac.compare_digest(str(row.auth or ""), str(auth)))
            if not mine:
                return False
        return bool(q.delete())


def subscriptions(user=None, *, org=None):
    """A user's subscriptions (every one with ``user=None`` and no ``org``)."""
    from pyweb.models import system
    ensure_tables()
    with system():
        q = PushSubscription.query()
        if user is not None:
            q = q.where(user_id=getattr(user, "id", user))
        if org is not None:
            q = q.where(org_id=getattr(org, "id", org))
        return list(q.order("id"))


# ------------------------------------------------------------------ sending

def message(title, body=None, *, url="/", tag=None, icon=None, data=None) -> bytes:
    """The JSON the service worker shows as a notification."""
    if not isinstance(url, str) or not url.startswith("/") or url.startswith("//"):
        raise ValueError("push url must be a path in this app, like '/orders/42'")
    payload = {"title": str(title), "url": url}
    if body:
        payload["body"] = str(body)
    if tag:
        payload["tag"] = str(tag)
    if icon:
        payload["icon"] = str(icon)
    if data:
        payload["data"] = data
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    if len(raw) > MAX_PAYLOAD:
        raise PushError(f"push message too long ({len(raw)} bytes, at most {MAX_PAYLOAD})")
    return raw


def deliver(subscription_id, payload, ttl=86400, urgency="normal", topic=None):
    """The job behind :func:`send`: one encrypted POST to the browser's push service."""
    from pyweb.http import FetchError, fetch
    from pyweb.models import system
    with system():
        sub = PushSubscription.get(subscription_id)
    if sub is None:
        return {"status": "gone"}
    body = encrypt(sub.p256dh, sub.auth, payload.encode() if isinstance(payload, str) else payload)
    headers = {"Authorization": vapid_header(sub.endpoint), "Content-Encoding": "aes128gcm",
               "Content-Type": "application/octet-stream", "TTL": str(int(ttl)), "Urgency": urgency}
    if topic:
        headers["Topic"] = topic
    allow = [urlparse(sub.endpoint).hostname] if urlparse(sub.endpoint).scheme == "http" else None
    try:
        resp = fetch(sub.endpoint, method="POST", data=body, headers=headers, timeout=10, max_bytes=16_000,
                     allow=allow)
    except FetchError as exc:
        raise PushError(f"push service unreachable: {exc}") from None      # the job retries
    if resp.status in (404, 410):
        with system():
            PushSubscription.where(id=sub.id).delete()
        log.info("push subscription %s is gone; deleted", sub.id)
        return {"status": "deleted"}
    if resp.status == 429 or resp.status >= 500:
        raise PushError(f"push service answered {resp.status}")            # the job retries later
    with system():
        if resp.ok:
            PushSubscription.where(id=sub.id).update(last_sent_at=dt.datetime.now(dt.timezone.utc), failures=0)
        else:
            PushSubscription.where(id=sub.id).update(failures=sub.failures + 1)
    if not resp.ok:
        log.warning("push to subscription %s refused: HTTP %s %s", sub.id, resp.status, resp.text[:200])
    return {"status": resp.status}


def _job():
    from pyweb import jobs
    if "pyweb.push.deliver" not in jobs.REGISTRY:
        jobs.job(deliver, name="pyweb.push.deliver", retries=5)
    return jobs.REGISTRY["pyweb.push.deliver"]


def _targets(to):
    if isinstance(to, PushSubscription):
        return [to]
    if isinstance(to, (list, tuple, set)):
        out = []
        for item in to:
            out.extend(_targets(item))
        return out
    return subscriptions(to)


def send(to, title, body=None, *, url="/", tag=None, icon=None, data=None, ttl=86400, urgency="normal",
         topic=None) -> int:
    """Notify ``to`` (a user or user id: every browser they turned notifications on in; a
    :class:`PushSubscription`; or a list of these). Queued as jobs; returns how many were queued.

    ``url``: the page a tap opens. ``tag``: a newer notification with the same tag replaces the
    older one. ``ttl``: seconds the push service keeps it for a browser that's offline.
    ``urgency``: very-low, low, normal or high (low saves phones' batteries).
    """
    if urgency not in URGENCY:
        raise ValueError(f"urgency must be one of {', '.join(URGENCY)}")
    if topic is not None and (len(topic) > 32 or not all(c.isalnum() or c in "-_" for c in topic)):
        raise ValueError("topic: at most 32 letters, digits, - or _")
    payload = message(title, body, url=url, tag=tag, icon=icon, data=data).decode()
    job = _job()
    targets = _targets(to)
    for sub in targets:
        job.enqueue(sub.id, payload, ttl=ttl, urgency=urgency, topic=topic)
    return len(targets)


def broadcast(title, body=None, *, org=None, **options) -> int:
    """Notify every subscribed browser (of ``org``, when given)."""
    return send(subscriptions(org=org), title, body, **options)


# ------------------------------------------------------------------ HTTP endpoints

def _json(status, data):
    from pyweb.runtime.server import Response
    return Response(status, json.dumps(data), {"Content-Type": "application/json", "Cache-Control": "no-store"})


def handle(server, req, cfg):
    """``/__pyweb/push/key`` (GET), ``/subscribe`` and ``/unsubscribe`` (POST, same-origin JSON)."""
    path = req.path.split("?")[0]
    if not cfg.push:
        return None
    try:
        _crypto()
    except PushError as exc:
        return _json(503, {"error": str(exc)})
    if path == "/__pyweb/push/key":
        if req.method not in ("GET", "HEAD"):
            return _json(405, {"error": "GET only"})
        return _json(200, {"key": public_key()})
    if path not in ("/__pyweb/push/subscribe", "/__pyweb/push/unsubscribe"):
        return None
    if req.method != "POST":
        return _json(405, {"error": "POST only"})
    hdr = {k.lower(): v for k, v in (req.headers or {}).items()}
    if "json" not in hdr.get("content-type", "").lower():
        return _json(415, {"error": "send JSON"})
    from pyweb.security import cross_site
    if cross_site(hdr):
        return _json(403, {"error": "cross-site request rejected"})
    if getattr(server, "rate_limit", None) is not None:
        from pyweb.runtime.server import client_ip
        ok, _retry = server.rate_limit.allow(f"push:{client_ip(req)}")
        if not ok:
            return _json(429, {"error": "too many requests"})
    if len(req.body or b"") > 8192:
        return _json(413, {"error": "too large"})
    try:
        data = json.loads(req.body or b"{}")
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        return _json(400, {"error": "bad JSON"})
    endpoint = data.get("endpoint")
    if not isinstance(endpoint, str):
        return _json(400, {"error": "endpoint missing"})
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    from pyweb import tenancy
    from pyweb.context import session
    payload = session.user() or {}
    if path.endswith("/unsubscribe"):
        sub_id = payload.get("sub")
        who = int(sub_id) if str(sub_id or "").isdigit() and not payload.get("api") else None
        return _json(200, {"ok": True, "deleted": unsubscribe(endpoint, auth=str(keys.get("auth") or ""), user=who)})
    if payload.get("api"):
        return _json(403, {"error": "API tokens can't subscribe browsers"})
    sub_id = payload.get("sub")
    user_id = int(sub_id) if isinstance(sub_id, int) or (isinstance(sub_id, str) and sub_id.isdigit()) else None
    try:
        row = subscribe(endpoint, keys.get("p256dh") or "", keys.get("auth") or "", user=user_id,
                        org=tenancy.current_org_id(), user_agent=(hdr.get("user-agent") or "")[:300] or None)
    except ValueError as exc:
        return _json(422, {"error": str(exc)})
    replaces = data.get("replaces")
    if isinstance(replaces, str) and replaces != endpoint:
        from pyweb.models import system
        with system():
            PushSubscription.where(endpoint_hash=_hash(replaces), user_id=user_id).delete()
    return _json(200, {"ok": True, "id": row.id})
