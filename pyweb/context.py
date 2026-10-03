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
    # Development only: stable across worker processes of the same project so
    # sessions survive reloads and multi-worker dev servers. Production must
    # set PYWEB_AUTH_SECRET (enforced when PYWEB_ENV=production).
    import getpass
    import hashlib
    import socket
    seed = f"pyweb-dev|{socket.gethostname()}|{os.getcwd()}|{getpass.getuser()}"
    return hashlib.sha256(seed.encode()).hexdigest()


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


def _secret(ctx):
    secret = ctx.auth_secret or os.environ.get("PYWEB_AUTH_SECRET")
    if secret:
        return secret
    if os.environ.get("PYWEB_ENV", "development") == "production":
        raise RuntimeError("PYWEB_AUTH_SECRET must be set in production to use sessions")
    return _dev_secret


class _SessionProxy:
    """``pyweb.session``: signed-cookie login sessions."""

    max_age = 7 * 24 * 3600

    def user(self):
        """The session payload (``{"sub": user_id, ...}``) or ``None``."""
        ctx = current()
        if not ctx._session_loaded:
            from pyweb import auth
            ctx._session = auth.session_from_request(ctx.request, _secret(ctx), self.max_age)
            ctx._session_loaded = True
        return ctx._session

    def login(self, user_id, **claims):
        from pyweb import auth
        ctx = current()
        payload = {"sub": user_id, **claims}
        token = auth.issue_session(payload, _secret(ctx), self.max_age)
        request.set_cookie(auth.SESSION_COOKIE, token, max_age=self.max_age)
        ctx._session = auth.verify_session(token, _secret(ctx), self.max_age)
        ctx._session_loaded = True
        return ctx._session

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
