"""pyweb.http.fetch: outbound requests that can't be pointed at internal addresses (SSRF)."""

import http.server
import socket
import threading
import time

import pytest

from pyweb import http as pwhttp
from pyweb.http import FetchError, fetch, public


@pytest.fixture(autouse=True)
def no_proxy(monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy",
              "PYWEB_FETCH_ALLOW"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def server():
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append((self.path, dict(self.headers)))
            if self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", self.path.split("to=", 1)[1])
                self.end_headers()
                return
            if self.path == "/big":
                body = b"x" * 3000
            elif self.path == "/slow":
                time.sleep(1.5)
                body = b"late"
            else:
                body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.seen = seen
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
                                "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "fd00:ec2::254", "fe80::1",
                                "::ffff:127.0.0.1", "::ffff:169.254.169.254", "2002:7f00:1::", "192.0.2.1",
                                "64:ff9b::a9fe:a9fe", "64:ff9b::7f00:1", "::a9fe:a9fe"])
def test_internal_addresses_are_not_public(ip):
    assert not public(ip)


def test_public_addresses_are():
    assert public("93.184.216.34") and public("2606:2800:220:1:248:1893:25c8:1946")


def test_local_servers_are_refused_unless_allowed(server, monkeypatch):
    with pytest.raises(FetchError, match="isn't a public address"):
        fetch(server.url + "/")
    assert server.seen == []                                     # never connected
    assert fetch(server.url + "/", allow=["127.0.0.0/8"]).json() == {"ok": True}
    monkeypatch.setenv("PYWEB_FETCH_ALLOW", "localhost, 10.0.0.0/8")
    port = server.server_address[1]
    assert fetch(f"http://localhost:{port}/").ok


def test_other_schemes_are_refused():
    for url in ("file:///etc/passwd", "ftp://example.com/x", "gopher://127.0.0.1:6379/_x", "http:///x"):
        with pytest.raises(FetchError, match="only http and https"):
            fetch(url)


def test_redirects_are_checked_at_every_hop(server):
    with pytest.raises(FetchError, match="169.254.169.254"):
        fetch(server.url + "/redirect?to=http://169.254.169.254/latest/meta-data/", allow=["127.0.0.0/8"])
    got = fetch(server.url + "/redirect?to=/final", allow=["127.0.0.0/8"])
    assert got.url.endswith("/final") and got.ok


def test_credentials_are_dropped_when_a_redirect_leaves_the_site(server, monkeypatch):
    port = server.server_address[1]
    monkeypatch.setenv("PYWEB_FETCH_ALLOW", "localhost,127.0.0.0/8")
    fetch(f"{server.url}/redirect?to=http://localhost:{port}/other",
          headers={"Authorization": "Bearer t", "Cookie": "a=b", "X-Keep": "1"})
    first, second = server.seen[0][1], server.seen[1][1]
    assert first["Authorization"] == "Bearer t"
    assert "Authorization" not in second and "Cookie" not in second and second["X-Keep"] == "1"


def test_it_connects_to_the_address_it_checked(server, monkeypatch):
    """A name is resolved once and checked; the connection goes to that address (no second lookup)."""
    port = server.server_address[1]
    real = socket.getaddrinfo
    answers = iter(["127.0.0.1", "10.9.9.9"])               # a rebinding DNS server: safe, then internal

    def fake(host, *a, **k):
        if host == "rebind.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))]
        return real(host, *a, **k)
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    got = fetch(f"http://rebind.test:{port}/", allow=["127.0.0.0/8"])
    assert got.ok and server.seen[-1][1]["Host"] == f"rebind.test:{port}"


def test_size_and_time_limits(server):
    with pytest.raises(FetchError, match="larger than 1000"):
        fetch(server.url + "/big", allow=["127.0.0.0/8"], max_bytes=1000)
    with pytest.raises(FetchError):
        fetch(server.url + "/slow", allow=["127.0.0.0/8"], timeout=0.5)


def test_bodies(server):
    got = fetch(server.url + "/p", method="POST", json={"a": 1}, allow=["127.0.0.0/8"])
    assert got.text == '{"ok": true}'
    assert server.seen[-1][1]["Content-Type"] == "application/json"


def test_through_a_proxy_literal_internal_addresses_are_still_refused(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setattr(pwhttp.urllib.request, "proxy_bypass", lambda host: False)
    for url in ("https://10.0.0.1/", "https://[::1]/", "https://169.254.169.254/"):
        with pytest.raises(FetchError, match="public address"):
            fetch(url)
