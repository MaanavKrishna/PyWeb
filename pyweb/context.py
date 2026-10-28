"""Per-request context for server code: ``request``, ``session``, ``redirect``.

Server functions and page bodies run inside a request. They can read it
and influence the response without threading a request object through
every call::

    from pyweb import request, session, redirect

    @server
    def login(email: str, password: str) -> bool:
        user = find_user(email)
        if user and verify_password(password, user["hash"]):
            session.login(user["id"], roles=user["roles"])
            return True
        return False

    @app.page("/admin")
    def Admin():
        if not session.user():
            return redirect("/login")
        ...

The context is a :class:`contextvars.ContextVar`, so it is correct under
threads (``pyweb serve``) and asyncio (ASGI) alike.
"""

from __future__ import annotations

import contextvars
import os
import urllib.parse

_current = contextvars.ContextVar("pyweb_request", default=None)


def _derive_dev_secret():
    """The key used while developing when PYWEB_AUTH_SECRET isn't set: random, kept in a file only you
    can read (``~/.cache/pyweb/dev-secret``) so sessions survive reloads and worker processes, or random
    per process when that can't be written. Never derived from anything an attacker could learn.
    ``pyweb serve`` and the ASGI app refuse to start without PYWEB_AUTH_SECRET (see config.require_secret)."""
    import secrets
    base = os.environ.get("PYWEB_CACHE_DIR") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"), "pyweb")
    path = os.path.join(base, "dev-secret")
    try:
        st = os.stat(path)
        if st.st_mode & 0o077 == 0 and (not hasattr(os, "getuid") or st.st_uid == os.getuid()):
            with open(path, encoding="utf-8") as fh:
                value = fh.read().strip()
            if len(value) >= 32:
                return value
    except OSError:
        pass
    value = secrets.token_hex(32)
    try:
        os.makedirs(base, mode=0o700, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value)
        os.replace(tmp, path)
    except OSError:
        pass                                        # read-only home: this process's own random key
    return value


_dev_secret = _derive_dev_secret()


class Redirect:
    """Return ``redirect(url)`` from a page to answer with a redirect."""

    def __init__(self, url, status=303):
        self.url = url
        self.status = status


def redirect(url, status=303):
    return Redirect(url, status)


class NotFound(Exception):
    """Raise from a page (or route param conversion) to answer 404."""


class BadRequest(Exception):
    """A query parameter is missing or has the wrong type: answered with 400."""


_page_head = contextvars.ContextVar("pyweb_page_head", default=None)

HEAD_FIELDS = ("title", "description", "image", "canonical", "noindex")


def head(*, title=None, description=None, image=None, canonical=None, noindex=None):
    """Set the page's ``<title>`` and meta tags from page (or layout) code.

    Values left as ``None`` keep what ``@app.page(...)`` or the app set. Call it
    while the page renders on the server, e.g. once a record is loaded::

        head(title=post.title, description=post.summary, image=post.cover)
    """
    target = _page_head.get()
    if target is None:
        raise RuntimeError("head() only works while a page renders (in a page or layout body)")
    for key, value in zip(HEAD_FIELDS, (title, description, image, canonical, noindex)):
        if value is not None:
            target[key] = value


class RequestContext:
    def __init__(self, request, *, auth_secret=None, secure_cookies=False):
        self.request = request
        self.auth_secret = auth_secret
        self.secure_cookies = secure_cookies
        self.set_cookies = []      # raw Set-Cookie header values
        self.toasts = []           # pyweb.ui.toast(...): shown by the page that gets this answer
        self._session = None
        self._session_loaded = False


def current():
    ctx = _current.get()
    if ctx is None:
        raise RuntimeError("no active request: request/session are only available while "
                           "PyWeb is handling a page or RPC call")
    return ctx


def activate(ctx):
    return _current.set(ctx)


def deactivate(token):
    _current.reset(token)


class _RequestProxy:
    """``pyweb.request``: the current HTTP request (read-only)."""

    @property
    def path(self):
        return current().request.path.split("?")[0]

    @property
    def method(self):
        return current().request.method

    @property
    def headers(self):
        return dict(current().request.headers or {})

    @property
    def cookies(self):
        return dict(current().request.cookies or {})

    @property
    def query(self):
        q = current().request.path.partition("?")[2]
        return {k: v[-1] for k, v in urllib.parse.parse_qs(q).items()}

    @property
    def id(self):
        return current().request.id

    def set_cookie(self, name, value, *, max_age=None, path="/", http_only=True,
                   same_site="Lax", secure=None):
        ctx = current()
        secure = ctx.secure_cookies if secure is None else secure
        parts = [f"{name}={value}", f"Path={path}", f"SameSite={same_site}"]
        if max_age is not None:
            parts.append(f"Max-Age={int(max_age)}")
        if http_only:
            parts.append("HttpOnly")
        if secure:
            parts.append("Secure")
        ctx.set_cookies.append("; ".join(parts))

    def __repr__(self):
        try:
            return f"<pyweb.request {self.method} {self.path}>"
        except RuntimeError:
            return "<pyweb.request (no active request)>"


def authorization(req):
    """The request's ``Authorization`` header (any letter case), or ""."""
    headers = getattr(req, "headers", None) or {}
    return next((v for k, v in headers.items() if k.lower() == "authorization"), "") or ""


def _api_token_payload(req):
    """An auth kit API token in ``Authorization: Bearer`` (server function calls only), as a session payload."""
    header = authorization(req)
    if not header.startswith("Bearer pwk_"):
        return None
    from pyweb.auth import KIT
    kit = KIT.get("kit")
    if kit is None:
        return None
    return kit.token_payload(header[len("Bearer "):].strip(), (getattr(req, "path", "") or "").split("?")[0])


def _secret(ctx):
    return resolve_secret(ctx.auth_secret)


def resolve_secret(explicit=None):
    """The signing secret: ``explicit``, else PYWEB_AUTH_SECRET, else (not in production) the dev key."""
    secret = explicit or os.environ.get("PYWEB_AUTH_SECRET")
    if secret:
        return secret
    if os.environ.get("PYWEB_ENV", "development") == "production":
        raise RuntimeError("PYWEB_AUTH_SECRET must be set in production to use sessions")
    return _dev_secret


def sign_key(purpose, ctx=None):
    """The key that signs new ``purpose`` tokens (see :mod:`pyweb.keys`)."""
    from . import keys
    return keys.signing_key(_secret(ctx or current()), purpose)


def verify_keys(purpose, ctx=None):
    """Every key a ``purpose`` token may be signed with: the current secret's, then previous secrets'."""
    from . import keys
    return keys.verify_keys(_secret(ctx or current()), purpose)


class _SessionProxy:
    """``pyweb.session``: signed-cookie login sessions."""

    #: Logged out after this long without a visit (seconds).
    max_age = 7 * 24 * 3600
    #: Logged out this long after signing in, however active (seconds).
    absolute_age = 30 * 24 * 3600
    #: A visit after this long renews the idle timer (the cookie is reissued).
    renew_after = 24 * 3600

    def user(self):
        """The session payload (``{"sub": user_id, ...}``) or ``None``."""
        ctx = current()
        if not ctx._session_loaded:
            from pyweb import auth
            payload = auth.session_from_request(ctx.request, verify_keys("session", ctx), self.max_age)
            if payload is not None and not auth.session_valid(payload, absolute_age=self.absolute_age):
                payload = None
            if payload is None:
                payload = _api_token_payload(ctx.request)
            ctx._session = payload
            ctx._session_loaded = True
            if payload is not None and auth.time.time() - payload.get("iat", 0) > self.renew_after:
                self._issue(ctx, {k: v for k, v in payload.items() if k != "iat"})
        return ctx._session

    def _issue(self, ctx, payload):
        from pyweb import auth
        token = auth.issue_session(payload, sign_key("session", ctx), self.max_age)
        request.set_cookie(auth.SESSION_COOKIE, token, max_age=self.max_age)
        ctx._session = auth.verify_session(token, sign_key("session", ctx), self.max_age)
        ctx._session_loaded = True
        return ctx._session

    def login(self, user_id, **claims):
        """Sign ``user_id`` in. Each login starts a new session (a fresh ``sid``)."""
        import secrets as _secrets
        import time as _time
        from pyweb import auth
        ctx = current()
        payload = {**claims, "sub": user_id, "sid": _secrets.token_urlsafe(16), "auth_time": int(_time.time())}
        versions = auth.session_versions()
        if versions is not None:
            payload["ver"] = versions.get(user_id)
        return self._issue(ctx, payload)

    def update(self, **claims):
        """Change claims of the current session (same session id); returns the new payload, or None."""
        ctx = current()
        payload = self.user()
        if payload is None:
            return None
        return self._issue(ctx, {**{k: v for k, v in payload.items() if k not in ("iat", "exp")}, **claims})

    def logout(self):
        from pyweb import auth
        ctx = current()
        request.set_cookie(auth.SESSION_COOKIE, "", max_age=0)
        ctx._session = None
        ctx._session_loaded = True

    def require(self, *roles):
        """Return the session or raise ``RPCError(unauthenticated/forbidden)``."""
        from pyweb.rpc import Code, RPCError
        user = self.user()
        if not user:
            raise RPCError(Code.AUTH, "login required")
        missing = [r for r in roles if r not in (user.get("roles") or [])]
        if missing:
            raise RPCError(Code.FORBIDDEN, f"missing role(s): {', '.join(missing)}")
        return user


request = _RequestProxy()
session = _SessionProxy()
