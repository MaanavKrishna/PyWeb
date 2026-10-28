"""Testing helpers.

* :class:`TestClient` — in-process HTTP client for a `.pyweb` app. Pages
  render exactly as in production, ``rpc()`` goes through the real RPC
  endpoint (validation, errors, cookies), and cookies persist across
  calls so sessions work::

      from pyweb.testing import TestClient

      client = TestClient("app.pyweb")
      assert "Todos" in client.get("/").text
      client.rpc("login", email="a@b.c", password="secret123")
      assert client.get("/account").status == 200

* :func:`serve` — run the app on a free local port for real-browser tests
  (e.g. Playwright)::

      with serve("app.pyweb") as url:
          page.goto(url)
"""

from __future__ import annotations

import contextlib
import json

from .rpc import RPCError


class TestResponse:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body)

    def header(self, name):
        name = name.lower()
        return next((v for k, v in self.headers if k.lower() == name), None)

    @property
    def html(self):
        """The body as a parsed document: ``.find("li.done")``, ``.find_all(...)``, ``.text``."""
        doc = getattr(self, "_doc", None)
        if doc is None:
            from .htmlquery import parse
            doc = self._doc = parse(self.text)
        return doc

    def find(self, selector):
        """The first element matching a CSS selector (``tag``, ``#id``, ``.class``, ``[attr=value]``,
        descendants with spaces), or None."""
        return self.html.find(selector)

    def find_all(self, selector):
        return self.html.find_all(selector)

    def __repr__(self):
        return f"<TestResponse {self.status}>"


class TestClient:
    __test__ = False  # not a pytest test class

    def __init__(self, app="app.pyweb", *, source=None, **server_kwargs):
        from .hosting import Site
        if source is not None:
            import os
            import tempfile
            tmp = tempfile.mkdtemp(prefix="pyweb-test-")
            app = os.path.join(tmp, "app.pyweb")
            with open(app, "w", encoding="utf-8") as fh:
                fh.write(source)
        self.site = Site(app, debug=True, **server_kwargs)
        self.cookies = {}

    @property
    def app(self):
        return self.site.app

    def _headers(self, extra=None):
        h = {"Host": "testserver"}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        h.update(extra or {})
        return h

    def _store_cookies(self, headers):
        for k, v in headers:
            if k.lower() != "set-cookie":
                continue
            pair = v.split(";", 1)[0]
            name, _, value = pair.partition("=")
            if "Max-Age=0" in v or not value:
                self.cookies.pop(name.strip(), None)
            else:
                self.cookies[name.strip()] = value.strip()

    def request(self, method, path, body=b"", headers=None):
        status, hdrs, raw = self.site.respond(method, path, self._headers(headers), body)
        if hasattr(raw, "snapshot"):  # an event stream: return what is available now
            raw = raw.snapshot()
        self._store_cookies(hdrs)
        return TestResponse(status, hdrs, raw)

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, data=None, headers=None):
        body = json.dumps(data).encode() if data is not None else b""
        return self.request("POST", path, body, {"Content-Type": "application/json", **(headers or {})})

    def render(self, name, **props):
        """A component (or page) of the app rendered on its own with ``props``: a parsed fragment
        (``.find(...)``, ``.text``). ``session`` and ``request`` work as in a real request."""
        from .htmlquery import parse
        from .runtime.server import Request
        loaded = self.site.app
        server = self.site.server

        def run():
            if name in loaded.compiled["pages"]:
                html = loaded.render(name, props, path="/")
                return html if isinstance(html, str) else ""
            if name not in loaded.ctx.components and name not in loaded.ctx.imported_components:
                raise KeyError(f"no component or page named {name!r}")
            return loaded.renderer("/").component_html(name, props, None)
        return parse(server.in_request(Request("GET", "/", self._headers()), run))

    def a11y(self, path="/"):
        """Accessibility problems in the page at ``path`` as rendered (images without ``alt``, fields
        without a label, buttons and links without a name, ...): a list of dicts; empty is good."""
        from .a11y import check_html
        return check_html(self.get(path).text)

    def put(self, path, data=None, headers=None):
        body = json.dumps(data).encode() if data is not None else b""
        return self.request("PUT", path, body, {"Content-Type": "application/json", **(headers or {})})

    def patch(self, path, data=None, headers=None):
        body = json.dumps(data).encode() if data is not None else b""
        return self.request("PATCH", path, body, {"Content-Type": "application/json", **(headers or {})})

    def delete(self, path, headers=None):
        return self.request("DELETE", path, b"", headers)

    def login(self, email="user@example.com", *, roles=(), name=None, org=None, org_role="owner", **claims):
        """Sign in as someone without going through the sign-in form; returns the user.

        With the auth kit (``app.use_auth()``) the account is created if it doesn't exist
        (with ``roles``) and signed in like a real sign-in. Without it, the session's user is
        ``{"sub": email, "roles": [...], ...claims}``.

        ``org=`` (with ``use_auth(orgs=True)``) makes them a member of that organization, with
        ``org_role``, and makes it their current one: an Organization, its id, or a name (an
        organization with that name is created when there isn't one).
        """
        from .context import session
        from .runtime.server import Request
        server = self.site.server
        kit = server._auth_kit()

        def sign_in():
            if kit is None:
                session.login(email, roles=list(roles), **({"name": name} if name else {}), **claims)
                return session.user()
            from .authkit import User
            from .models import system
            with system():
                user = User.where(email=email.strip().lower()).first()
                if user is None:
                    user = kit.create_user(email, name=name or email.split("@")[0], roles=roles, verified=True)
                elif roles and set(roles) - set(user.roles or []):
                    user.roles = sorted(set(user.roles or []) | set(roles))
                    user.save(validate=False)
                signed_in = kit.login(user, method="test")
            if org is not None:
                _join(kit, user, org, org_role)
            return signed_in

        req = Request("GET", "/", self._headers())
        from .context import current
        captured = {}

        def run():
            result = sign_in()
            captured["cookies"] = list(current().set_cookies)
            return result
        from .db import request_scope
        with request_scope():
            user = server.in_request(req, run)
        self._store_cookies([("Set-Cookie", c) for c in captured["cookies"]])
        return user

    def logout(self):
        """Forget the session cookie (sign out)."""
        self.cookies.clear()

    def rpc(self, function, /, **args):
        """Call a server function like the browser does; raise RPCError on failure.

        A function that ``yield``s returns the list of values it streamed.
        """
        resp = self.post(f"/__pyweb/rpc/{function}", {"args": args})
        if "x-ndjson" in (resp.header("Content-Type") or ""):
            chunks = []
            for line in resp.body.decode().splitlines():
                msg = json.loads(line) if line.strip() else {}
                if "chunk" in msg:
                    chunks.append(msg["chunk"])
                elif "error" in msg:
                    err = msg["error"]
                    raise RPCError(err.get("code", "internal"), err.get("message", ""), status=resp.status,
                                   details=err.get("details"))
            return chunks
        payload = resp.json() if resp.body else {}
        if resp.status != 200 or "error" in payload:
            err = payload.get("error") or {}
            raise RPCError(err.get("code", f"http_{resp.status}"), err.get("message", ""),
                           status=resp.status, details=err.get("details"))
        return payload.get("result")


def _join(kit, user, org, role):
    """Make ``user`` a member of ``org`` (created by name if needed) with ``role``; switch to it."""
    if not getattr(kit, "orgs_enabled", False):
        raise RuntimeError("login(org=...) needs app.use_auth(orgs=True)")
    from .authkit.orgs import Membership, Organization
    from .models import system
    with system():
        if isinstance(org, str):
            found = Organization.where(name=org).first()
            org = found or kit.create_org(org, owner=user)
        elif not isinstance(org, Organization):
            org = Organization.get(org)
        member = Membership.where(org_id=org.id, user=user).first()
        if member is None:
            member = Membership.create(org=org, user=user, role=role)
        elif member.role != role:
            member.role = role
            member.save(validate=False)
    kit.switch_org(org)
    return org


def as_org(org, role=None):
    """``with as_org(org):`` runs code (queries, server functions called directly, jobs) for that
    organization, as a request from one of its members would: tenant Models see its rows only."""
    from .tenancy import org_scope
    return org_scope(org, role)


def assert_snapshot(value, name, *, folder=None):
    """Compare ``value`` (text, or anything with ``str()``) with ``__snapshots__/<name>.txt`` next to
    the calling test. The first run writes the file; ``PYWEB_UPDATE_SNAPSHOTS=1`` rewrites them all.
    A difference fails with a diff."""
    import difflib
    import inspect
    import os
    import re as _re
    text = value if isinstance(value, str) else str(value)
    if folder is None:
        caller = inspect.stack()[1].filename
        folder = os.path.join(os.path.dirname(os.path.abspath(caller)), "__snapshots__")
    if not _re.fullmatch(r"[\w.-]+", name):
        raise ValueError("snapshot names are letters, digits, '.', '_' and '-'")
    path = os.path.join(folder, name + ".txt")
    if os.environ.get("PYWEB_UPDATE_SNAPSHOTS") == "1" or not os.path.exists(path):
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return
    with open(path, encoding="utf-8") as fh:
        expected = fh.read()
    if expected != text:
        diff = "".join(difflib.unified_diff(expected.splitlines(True), text.splitlines(True),
                                            "snapshot", "now"))
        raise AssertionError(f"{name} doesn't match {path} (PYWEB_UPDATE_SNAPSHOTS=1 to accept):\n{diff}")


@contextlib.contextmanager
def serve(app="app.pyweb", *, host="127.0.0.1", **site_kwargs):
    """Serve ``app`` (a `.pyweb` file or built dist) on a free port with PyWeb's server; yield its base URL."""
    from .hosting import Site
    from .net.server import Running

    running = Running(Site(app, **site_kwargs), host=host)
    try:
        yield running.url
    finally:
        running.stop(timeout=2.0)


class Factory:
    """Make test rows with sensible defaults::

        users = Factory(User, email=lambda n: f"user{n}@example.com", name="Test")
        posts = Factory(Post, title=lambda n: f"Post {n}", author=users)

        post = posts.create()                 # also creates its author
        drafts = posts.create_batch(3, published=False)
        acme_projects = projects.for_org(acme)  # tenant Models: rows in that organization

    A default can be a value, a function of the row number ``n``, or another
    Factory (which creates the related row).
    """

    def __init__(self, model, **defaults):
        self.model = model
        self.defaults = defaults
        self._count = [0]                    # shared with for_org() copies, so numbers don't repeat
        self.org = None

    @property
    def n(self):
        return self._count[0]

    def for_org(self, org):
        """The same factory, making its rows in ``org`` (for ``tenant=True`` Models)."""
        other = Factory(self.model, **self.defaults)
        other._count = self._count
        other.org = org
        return other

    def _scope(self):
        return as_org(self.org) if self.org is not None else contextlib.nullcontext()

    def values(self, **overrides):
        self._count[0] += 1
        out = {}
        for key, value in self.defaults.items():
            if key in overrides:
                continue
            if isinstance(value, Factory):
                value = value.create()
            elif callable(value) and not isinstance(value, type):
                value = value(self._count[0])
            out[key] = value
        out.update(overrides)
        return out

    def build(self, **overrides):
        """A new, unsaved row."""
        return self.model(**self.values(**overrides))

    def create(self, **overrides):
        """A new, saved row."""
        with self._scope():
            return self.model.create(**self.values(**overrides))

    def create_batch(self, count, **overrides):
        return [self.create(**overrides) for _ in range(count)]
