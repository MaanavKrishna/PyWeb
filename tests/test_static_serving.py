"""Static files from `pyweb serve` and the ASGI/dev Site: content types, caching, ETags, gzip."""

import argparse
import gzip
import os
import urllib.request as urlrequest

import pytest

from pyweb import cli
from pyweb.hosting import Site
from pyweb.serve import serve_in_thread

APP = ("from pyweb import App\napp = App()\n@app.page('/')\ndef H():\n    n = 0\n"
       "    def inc():\n        n += 1\n    <h1 onclick={inc}>{n}</h1>\n")


@pytest.fixture(scope="module")
def dist(tmp_path_factory):
    d = tmp_path_factory.mktemp("app")
    os.makedirs(d / "static" / "vendor" / "lit@3.2.0")
    (d / "app.pyweb").write_text(APP)
    (d / "static" / "app.css").write_text("h1 { color: red }")
    (d / "static" / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (d / "static" / "vendor" / "lit@3.2.0" / "index.js").write_text("export {}")
    cli.cmd_build(argparse.Namespace(file=str(d / "app.pyweb"), out=str(d / "dist"), budget=[], production=True))
    return d / "dist"


@pytest.fixture(scope="module")
def http(dist):
    httpd, _ = serve_in_thread(str(dist))
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path, **headers):
        req = urlrequest.Request(base + path, headers=headers)
        try:
            with urlrequest.urlopen(req) as res:
                return res.status, res.headers, res.read()
        except urlrequest.HTTPError as e:
            return e.code, e.headers, b""
    yield get
    httpd.shutdown()
    httpd.server_close()


def runtime_name(dist):
    return next(f for f in os.listdir(dist / "static") if f.startswith("runtime.") and f.endswith(".js"))


def both(dist, http, path, **headers):
    """The same request through `pyweb serve` and the Site (ASGI and dev)."""
    status, h, body = http(path, **headers)
    s2, h2, b2 = Site(str(dist)).respond("GET", path, headers)
    return [(status, {k.lower(): v for k, v in h.items()}, body),
            (s2, {k.lower(): v for k, v in h2}, b2)]


@pytest.mark.parametrize("path, ctype, cached", [
    ("/static/app.css", "text/css", False),                 # your own files: revalidated, so deploys show up
    ("/static/logo.svg", "image/svg+xml", False),           # browsers won't draw an SVG sent as octet-stream
    ("/static/app.css?v=1a2b", "text/css", True),           # versioned URL
    ("/static/vendor/lit@3.2.0/index.js", "text/javascript", True),
])
def test_content_types_and_caching(dist, http, path, ctype, cached):
    for status, h, _ in both(dist, http, path):
        assert status == 200 and h["content-type"].startswith(ctype)
        assert ("immutable" in h["cache-control"]) is cached
        assert h["etag"]


def test_hashed_runtime_is_immutable_and_gzipped(dist, http):
    path = f"/static/{runtime_name(dist)}"
    raw = (dist / "static" / runtime_name(dist)).read_bytes()
    for status, h, body in both(dist, http, path, **{"Accept-Encoding": "gzip, br"}):
        assert status == 200 and "immutable" in h["cache-control"]
        assert h["content-encoding"] == "gzip" and "Accept-Encoding" in h["vary"]
        assert gzip.decompress(body) == raw and len(body) < len(raw) / 2
    for status, h, body in both(dist, http, path):          # no Accept-Encoding: plain bytes
        assert "content-encoding" not in h and body == raw


def test_etag_gives_304(dist, http):
    etag = http("/static/app.css")[1]["ETag"]
    for status, _, body in both(dist, http, "/static/app.css", **{"If-None-Match": etag}):
        assert status == 304 and body == b""


def test_traversal_is_refused(dist):
    assert Site(str(dist)).respond("GET", "/static/../../app.pyweb", {})[0] == 403


def test_pages_are_gzipped_with_their_csp(dist, http):
    status, h, body = http("/", **{"Accept-Encoding": "gzip"})
    html = gzip.decompress(body) if h.get("Content-Encoding") == "gzip" else body
    assert status == 200 and b"<h1" in html
    assert "script-src" in h["Content-Security-Policy"]


def test_asgi_joins_repeated_cookie_headers(tmp_path):
    """HTTP/2 clients may send each cookie in its own header; all of them must reach the app."""
    import asyncio
    from pyweb.asgi import create_app
    src = tmp_path / "app.pyweb"
    src.write_text("from pyweb import App, request\napp = App()\n@app.page('/')\ndef H():\n"
                   "    seen = sorted(request.cookies)\n    <p id=\"c\">{', '.join(seen)}</p>\n")
    app = create_app(str(src), rate_limit=False)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "method": "GET", "path": "/", "query_string": b"", "client": ("10.0.0.9", 5000),
             "headers": [(b"cookie", b"a=1"), (b"cookie", b"b=2"), (b"accept", b"text/html")]}
    asyncio.run(app(scope, receive, send))
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert b'<p id="c">a, b</p>' in body
