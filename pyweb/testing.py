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
import threading

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


@contextlib.contextmanager
def serve(app="app.pyweb", *, host="127.0.0.1", **site_kwargs):
    """Serve ``app`` (a `.pyweb` file or built dist) on a free port; yield its base URL."""
    import http.server

    from .hosting import Site
    from .serve import ThreadedServer

    site = Site(app, **site_kwargs)

    class Handler(http.server.BaseHTTPRequestHandler):
        def _go(self, method, body=b""):
            from .hosting import write_http
            status, headers, raw = site.respond(method, self.path, dict(self.headers), body)
            write_http(self, status, headers, raw, head=method == "HEAD")

        def do_GET(self):  # noqa: N802
            self._go("GET")

        def do_HEAD(self):  # noqa: N802
            self._go("HEAD")

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length", 0) or 0)
            self._go("POST", self.rfile.read(n) if n else b"")

        def log_message(self, *a):
            pass

    httpd = ThreadedServer((host, 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
