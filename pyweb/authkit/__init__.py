"""The auth kit: accounts, sign-in and an admin, in one line.

::

    app = App(database="sqlite:///app.db")
    auth = app.use_auth(providers=["github"])

    @app.page("/dashboard", login=True)
    def Dashboard():
        user = auth.user()
        <p>Hello {user.name}</p>

    @server(login=True, roles=["admin"])
    def delete_everything(): ...

It adds:

* pages: ``/login``, ``/signup``, ``/logout``, ``/reset``, ``/account``
  (password, passkeys, two-factor codes, linked accounts, sessions,
  activity) and ``/admin`` (every Model, for users with the ``admin`` role).
  Define a page at the same route to replace one; the kit's Python API
  (:meth:`AuthKit.authenticate`, :meth:`AuthKit.login`, ...) does the work.
* sign-in with a password, an emailed link, a passkey, or GitHub / Google /
  Microsoft, plus optional authenticator-app codes.
* tables (``users``, ``auth_identities``, ``auth_credentials``,
  ``auth_tokens``, ``auth_events``) that migrate like your own Models.

Security defaults: scrypt/argon2 password hashes upgraded at sign-in, the
breached-password check in production, slowing down guessing per account
and per IP, the same answer and timing whether or not an email has an
account, single-use hashed tokens that expire in 15 minutes, a reset signs
out every other session, OAuth with PKCE and state that never attaches
itself to an existing account without a sign-in, and an audit log.
"""

from __future__ import annotations

import base64
import datetime as dt
import hmac
import json
import logging
import os
import time
import urllib.parse

from pyweb.rules import EMAIL_RE, ValidationError

from . import passwords
from .models import MODELS, AuthEvent, AuthSession, AuthToken, Credential, Identity, User, device_label, ip_prefix
from .privacy import PrivacyMixin
from .teams import TeamsMixin
from .throttle import Throttle

__all__ = ["AuthKit", "User", "Identity", "Credential", "AuthToken", "AuthEvent", "current_user", "MODELS"]

from pyweb.auth import KIT, current_user

log = logging.getLogger("pyweb.auth")

PENDING_2FA = "__pw_2fa"
OAUTH_COOKIE = "__pw_oauth"
WEBAUTHN_COOKIE = "__pw_webauthn"


def secrets_randbelow(n):
    import secrets
    return secrets.randbelow(n)


def _auth_absolute_age():
    from pyweb import auth as _auth
    return _auth.ABSOLUTE_AGE


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def system_writes():
    from pyweb.models import system
    return system()


_CHECK_SQL: dict = {}


class _DBSessionVersions:
    """Session versions in the users table: durable and shared by every server."""

    def __init__(self, kit):
        self.kit = kit

    def get(self, user_id):
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return 0
        try:
            row = User.where(id=user_id).values("session_version")
        except Exception:  # noqa: BLE001
            # Fail closed: if the check can't run, treat every session as revoked rather than
            # letting a signed-out (or demoted) session back in while the database is unwell.
            log.warning("session version check failed; refusing the session", exc_info=True)
            return 2 ** 62
        return int(row[0]["session_version"] or 0) if row else 0

    def check(self, payload):
        """Is this session still good: not older than a sign-out everywhere (the user's version) and
        not signed out on its own (its row in auth_sessions)? One query; touches last_seen_at."""
        sid = payload.get("sid")
        if not sid:
            return int(payload.get("ver", 0) or 0) >= self.get(payload.get("sub"))
        try:
            user_id = int(payload.get("sub"))
        except (TypeError, ValueError):                    # not one of the kit's users: versions only
            return int(payload.get("ver", 0) or 0) >= self.get(payload.get("sub"))
        from pyweb.db.dialect import dialect_of
        db = User._database()
        dialect = dialect_of(db)
        q = dialect.quote
        sql = _CHECK_SQL.get(dialect.name)
        if sql is None:
            sql = _CHECK_SQL[dialect.name] = (
                f"SELECT u.{q('session_version')}, s.{q('id')}, s.{q('revoked_at')}, s.{q('last_seen_at')} "
                f"FROM {q('users')} u LEFT JOIN {q('auth_sessions')} s ON s.{q('user_id')} = u.{q('id')} "
                f"AND s.{q('sid_hash')} = ? WHERE u.{q('id')} = ?")
        digest = AuthSession.digest(sid)
        try:
            row = db.execute(sql, (digest, user_id)).fetchone()
        except Exception:  # noqa: BLE001 - e.g. an app upgraded without migrating auth_sessions yet
            if not getattr(self, "_warned", False):
                self._warned = True
                log.warning("couldn't read auth_sessions (run `pyweb db diff` and `pyweb db upgrade`); "
                            "checking sessions by version only", exc_info=True)
            return int(payload.get("ver", 0) or 0) >= self.get(user_id)
        if row is None:
            return False                                   # no such user
        version, sid_row, revoked, seen = row
        if int(payload.get("ver", 0) or 0) < int(version or 0) or revoked is not None:
            return False
        if sid_row is not None:
            self._touch(db, q, sid_row, seen)
        return True

    @staticmethod
    def _touch(db, q, sid_row, seen):
        from pyweb.db.dialect import dialect_of
        now = _now()
        if isinstance(seen, str):
            try:
                seen = dt.datetime.fromisoformat(seen)
            except ValueError:
                seen = None
        if seen is not None and seen.tzinfo is None:
            seen = seen.replace(tzinfo=dt.timezone.utc)
        if seen is not None and (now - seen).total_seconds() < 300:
            return                                         # at most one write per session every 5 minutes
        try:
            col = AuthSession._meta.field("last_seen_at")
            db.execute(f"UPDATE {q('auth_sessions')} SET {q('last_seen_at')} = ? WHERE {q('id')} = ?",
                       (col.to_db(now, dialect_of(db)), sid_row))
        except Exception:  # noqa: BLE001 - last seen is a convenience, never a reason to fail a request
            pass

    def bump(self, user_id):
        from pyweb.db.dialect import dialect_of
        db = User._database()
        q = dialect_of(db).quote
        db.execute(f"UPDATE {q('users')} SET {q('session_version')} = {q('session_version')} + 1 "
                   f"WHERE {q('id')} = ?", (int(user_id),))
        return self.get(user_id)


class AuthKit(TeamsMixin, PrivacyMixin):
    def __init__(self, app, db=None, *, methods=("password", "magic_link", "passkey"), providers=(),
                 signup="verify", two_factor=True, admin=True, after_login="/", after_logout="/",
                 site_name=None, rp_id=None, origin=None, orgs=False):
        from pyweb import auth as _auth
        from pyweb import models as M
        if db is not None:
            M.use_database(db)
        self.app = app
        self.methods = tuple(methods)
        if signup not in ("verify", "instant", False, None):
            raise ValueError('signup must be "verify", "instant" or False')
        self.signup = signup or None
        self.two_factor = two_factor
        self.admin_enabled = admin
        self.after_login = after_login
        self.after_logout = after_logout
        self.site_name = site_name or getattr(app, "title", None) or "PyWeb"
        self.rp_id = rp_id or os.environ.get("PYWEB_RP_ID")
        self.origin = origin or os.environ.get("PYWEB_ORIGIN")
        from .oauth import Provider
        self.providers = {}
        for p in providers or ():
            prov = p if isinstance(p, Provider) else Provider(p) if isinstance(p, str) else Provider(**p)
            self.providers[prov.name] = prov
        self.throttle = Throttle()
        self.mail_throttle = Throttle(free=3, cap=3600)
        _auth.use_session_versions(_DBSessionVersions(self))
        app.models.extend(m for m in MODELS if m not in app.models)
        from pyweb import tenancy
        tenancy.request_resolver = None
        if orgs:
            self._enable_orgs()
        KIT["kit"] = self

    # ------------------------------------------------------------- users
    def user(self):
        """The signed-in :class:`User` for this request, or None (cached per request)."""
        from pyweb.context import current, session
        try:
            ctx = current()
        except RuntimeError:
            return None
        if hasattr(ctx, "_pw_user"):
            return ctx._pw_user
        payload = session.user()
        user = None
        if payload and "sub" in payload:
            user = User.get(payload["sub"])
            if user is not None and not user.is_active:
                user = None
        ctx._pw_user = user
        return user

    def require(self, *roles, fresh=None):
        """The signed-in user, or raise (401 / 403) for a server function."""
        from pyweb.rpc import Code, RPCError
        user = self.user()
        if user is None:
            raise RPCError(Code.AUTH, "sign in first")
        if roles and not user.has_role(*roles):
            raise RPCError(Code.FORBIDDEN, "you don't have access to this")
        if fresh and not self.fresh(fresh):
            raise RPCError(Code.AUTH, "please sign in again to continue")
        return user

    def fresh(self, seconds):
        from pyweb.context import session
        payload = session.user() or {}
        return time.time() - payload.get("auth_time", 0) <= seconds

    def create_user(self, email, password=None, *, name="", roles=(), verified=False):
        email = email.strip().lower()
        user = User(email=email, name=name, roles=list(roles), email_verified=verified,
                    password_hash=passwords.hash_password(password) if password else None)
        user.save()
        return user

    def set_password(self, user, password):
        problem = passwords.problem(password, email=user.email, name=user.name)
        if problem:
            raise ValidationError({"password": problem})
        user.password_hash = passwords.hash_password(password)
        user.save(validate=False)
        self.revoke(user)

    def set_roles(self, user, roles):
        """Change a user's roles; their sessions end so the change applies at once."""
        old = list(user.roles or [])
        user.roles = sorted(set(roles))
        user.save()
        if sorted(old) != user.roles:
            self.event("roles_changed", user=user, detail=f"{','.join(old)} -> {','.join(user.roles)}")
            self.revoke(user)

    def revoke(self, user):
        from pyweb import auth as _auth
        _auth.revoke_user(user.id)
        try:
            with system_writes():
                AuthSession.where(user=user, revoked_at__isnull=True).update(revoked_at=_now())
        except Exception:  # noqa: BLE001 - the version bump above already signed every session out
            log.warning("couldn't mark sessions signed out", exc_info=True)

    def authenticate(self, email, password):
        """The user for this email and password, or raise ValidationError (same message either way).

        Slows down repeated failures per account and per IP address.
        """
        email = (email or "").strip().lower()
        ip = self._ip()
        wait = max(self.throttle.wait("acct:" + email), self.throttle.wait("ip:" + ip))
        if wait:
            self.event("login_throttled", email=email, ok=False)
            raise ValidationError({"__all__": f"Too many attempts. Try again in {wait} seconds."})
        user = User.where(email=email).first() if email else None
        if user is None or not user.password_hash or not user.is_active:
            passwords.burn_time(password)
            ok = False
        else:
            ok = passwords.verify_password(password or "", user.password_hash)
        if not ok:
            self.throttle.fail("acct:" + email)
            self.throttle.fail("ip:" + ip)
            self.event("login_failed", user=user if user and user.is_active else None, email=email, ok=False)
            raise ValidationError({"__all__": "That email and password don't match."})
        self.throttle.clear("acct:" + email)
        if passwords.needs_rehash(user.password_hash):
            user.password_hash = passwords.hash_password(password)
            user.save(validate=False)
        return user

    def login(self, user, method="password"):
        """Start a session for ``user`` (a new session id every time)."""
        from pyweb.context import current, session
        payload = session.login(user.id, roles=list(user.roles or []), name=user.name)
        user.last_login_at = _now()
        user.save(validate=False)
        self._record_session(user, (payload or {}).get("sid"))
        try:
            current()._pw_user = user
        except RuntimeError:
            pass
        self.event("login", user=user, detail=method)
        return user

    def _record_session(self, user, sid):
        if not sid:
            return
        try:
            from pyweb.context import request
            ua, ip = request.headers.get("User-Agent") or "", self._ip()
        except RuntimeError:
            ua, ip = "", None
        try:
            with system_writes():
                if secrets_randbelow(100) == 0:                     # now and then, forget old sessions
                    AuthSession.where(created_at__lt=_now() - dt.timedelta(seconds=_auth_absolute_age() + 86400)
                                      ).delete()
                AuthSession.create(user=user, sid_hash=AuthSession.digest(sid), device=device_label(ua)[:120],
                                   ip=ip_prefix(ip), last_seen_at=_now())
        except Exception:  # noqa: BLE001 - without the table (not migrated yet) sign-in still works
            log.warning("couldn't record the session in auth_sessions", exc_info=True)

    def sessions(self, user):
        """``user``'s signed-in devices, newest first (rows of :class:`AuthSession`)."""
        cutoff = _now() - dt.timedelta(seconds=_auth_absolute_age())
        with system_writes():
            return AuthSession.where(user=user, revoked_at__isnull=True, created_at__gt=cutoff).order("-id").all()

    def end_session(self, user, session_id):
        """Sign out one of ``user``'s devices (True if it was theirs and still signed in)."""
        with system_writes():
            return AuthSession.where(id=session_id, user=user, revoked_at__isnull=True).update(revoked_at=_now()) == 1

    def current_session_hash(self):
        from pyweb.context import session
        payload = session.user() or {}
        return AuthSession.digest(payload["sid"]) if payload.get("sid") else None

    def logout(self):
        from pyweb.context import current, session
        user = self.user()
        digest = self.current_session_hash()
        if digest is not None:
            try:
                with system_writes():
                    AuthSession.where(sid_hash=digest).update(revoked_at=_now())
            except Exception:  # noqa: BLE001
                log.warning("couldn't mark the session signed out", exc_info=True)
        session.logout()
        try:
            current()._pw_user = None
        except RuntimeError:
            pass
        if user is not None:
            self.event("logout", user=user)

    def event(self, kind, *, user=None, email=None, ok=True, detail=None):
        from pyweb.context import request
        from pyweb.telemetry import instruments
        instruments.auth_events.inc(kind=kind, ok="true" if ok else "false")
        try:
            ua = (request.headers.get("User-Agent") or "")[:300]
            ip = self._ip()
        except RuntimeError:
            ua, ip = None, None
        values = dict(user_id=getattr(user, "id", None), kind=kind, ok=ok,
                      email=(email or (user.email if user else None) or "")[:254] or None,
                      ip=(ip or "")[:64] or None, user_agent=ua, detail=(detail or None) and str(detail)[:300])

        def write():
            try:
                with system_writes():
                    AuthEvent.create(**values)
            except Exception:  # noqa: BLE001 - the audit log must never break sign-in
                import logging
                logging.getLogger("pyweb.auth").exception("couldn't record auth event %s", kind)

        try:
            # Inside a request's transaction the entry is written once it ends, committed or rolled
            # back: a failed sign-in is recorded even when the request fails.
            AuthEvent._database().after_end(write)
        except Exception:  # noqa: BLE001 - the audit log must never break sign-in
            import logging
            logging.getLogger("pyweb.auth").exception("couldn't record auth event %s", kind)

    # ------------------------------------------------------------- emails
    def _link(self, path):
        """An absolute link for an email. In production it never comes from the request's Host header,
        which anyone can set: a reset link pointing at an attacker's site would hand them the token."""
        if not self.origin:
            from pyweb.config import settings
            if settings().production:
                raise RuntimeError("set PYWEB_ORIGIN (e.g. https://example.com) so emailed links "
                                   "point at your site")
        return self._origin() + path

    def _mail(self, to, subject, text):
        from pyweb import mail
        mail.send(to, f"{subject} · {self.site_name}", text)

    def send_reset(self, email):
        """Email a reset link if an account exists. Always looks the same to the caller."""
        email = (email or "").strip().lower()
        user = User.where(email=email).first() if EMAIL_RE.match(email) else None
        if user is None or not user.is_active or self.mail_throttle.wait("mail:" + email):
            passwords.burn_time("")
            return
        self.mail_throttle.fail("mail:" + email)
        token = AuthToken.issue("reset", user=user, email=email)
        self.event("reset_requested", user=user)
        self._mail(email, "Reset your password",
                   f"Someone (hopefully you) asked to reset the password for {email}.\n\n"
                   f"Choose a new one here (the link works once, for 15 minutes):\n{self._link('/reset/' + token)}\n\n"
                   "If it wasn't you, ignore this email: your password stays the same.")

    def send_magic_link(self, email, next_url=""):
        email = (email or "").strip().lower()
        if not EMAIL_RE.match(email) or self.mail_throttle.wait("mail:" + email):
            return
        user = User.where(email=email).first()
        if user is None and not self.signup:
            passwords.burn_time("")
            return
        if user is not None and not user.is_active:
            return
        self.mail_throttle.fail("mail:" + email)
        token = AuthToken.issue("magic", user=user, email=email, data={"next": next_url})
        self._mail(email, "Your sign-in link",
                   f"Sign in to {self.site_name}:\n{self._link('/magic/' + token)}\n\n"
                   "The link works once, for 15 minutes. If you didn't ask for it, ignore this email.")

    def start_signup(self, email, password, name=""):
        """Sign-up with email confirmation. Returns None; an email always follows (or not, if throttled)."""
        email = (email or "").strip().lower()
        errors = {}
        if not EMAIL_RE.match(email):
            errors["email"] = "must be a valid email address"
        problem = passwords.problem(password, email=email, name=name)
        if problem:
            errors["password"] = problem
        if len(name) > 80:
            errors["name"] = "must be at most 80 characters"
        if errors:
            raise ValidationError(errors)
        if self.mail_throttle.wait("mail:" + email):
            return
        self.mail_throttle.fail("mail:" + email)
        existing = User.where(email=email).first()
        if existing is not None:
            passwords.burn_time(password)
            self._mail(email, "You already have an account",
                       f"Someone tried to sign up with {email}, which already has an account.\n\n"
                       f"Sign in: {self._link('/login')}\nForgot your password? {self._link('/reset')}\n\n"
                       "If it wasn't you, you can ignore this email.")
            return
        token = AuthToken.issue("signup", email=email, ttl=24 * 3600,
                                data={"name": name, "password_hash": passwords.hash_password(password)})
        self._mail(email, "Confirm your email",
                   f"Welcome to {self.site_name}! Confirm your email to finish signing up:\n"
                   f"{self._link('/verify/' + token)}\n\nThe link works once, for 24 hours.")

    # ------------------------------------------------------------- helpers
    def _ip(self):
        from pyweb.context import current
        from pyweb.runtime.server import client_ip
        try:
            return client_ip(current().request)
        except RuntimeError:  # called from a script, job or test: no request
            return "local"

    def _origin(self):
        if self.origin:
            return self.origin.rstrip("/")
        from pyweb.config import settings
        from pyweb.context import request
        headers = {k.lower(): v for k, v in request.headers.items()}
        trust = settings().trust_proxy
        host = (headers.get("x-forwarded-host") if trust else None) or headers.get("host") or "localhost"
        scheme = (headers.get("x-forwarded-proto") if trust else None) or \
            ("https" if settings().cookie_secure else "http")
        return f"{scheme.split(',')[0].strip()}://{host.split(',')[0].strip()}"

    def _rp_id(self):
        return self.rp_id or urllib.parse.urlparse(self._origin()).hostname

    def _sign(self, purpose, data, ttl):
        from pyweb.context import sign_key
        payload = _b64(json.dumps({"d": data, "exp": int(time.time()) + ttl}, separators=(",", ":")).encode())
        mac = hmac.new(sign_key(purpose).encode(), payload.encode(), "sha256").hexdigest()[:40]
        return f"{payload}.{mac}"

    def _unsign(self, purpose, token):
        from pyweb.context import verify_keys
        if not token or "." not in token:
            return None
        payload, mac = token.rsplit(".", 1)
        if not any(hmac.compare_digest(hmac.new(k.encode(), payload.encode(), "sha256").hexdigest()[:40], mac)
                   for k in verify_keys(purpose)):
            return None
        try:
            data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except ValueError:
            return None
        if data.get("exp", 0) < time.time():
            return None
        return data.get("d")

    # ---------------------------------------------------------------- HTTP
    def handle(self, server, req):
        """Answer a request for one of the kit's routes, or return None."""
        from . import pages
        return pages.dispatch(self, server, req)


def use_auth(app, db=None, **options):
    """``app.use_auth(...)``: see :class:`AuthKit`."""
    kit = AuthKit(app, db, **options)
    app.auth = kit
    return kit


def _safe_next(url, default="/"):
    from pyweb.auth import safe_next
    return safe_next(url, default)


def _valid_email(email):
    return bool(EMAIL_RE.match(email or ""))

