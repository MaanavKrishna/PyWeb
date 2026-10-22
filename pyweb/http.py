"""Outbound HTTP that is safe to point at addresses other people give you.

``fetch(url)`` is for URLs that come from users or other systems: webhook
endpoints, link previews, avatars, imports. A plain ``urlopen(url)`` there
lets anyone make your server request ``http://169.254.169.254/`` (cloud
credentials), ``http://localhost:6379/`` or your internal admin pages: a
server-side request forgery (SSRF). ``fetch``:

* only speaks ``http`` and ``https``;
* resolves the host and refuses addresses that aren't public: loopback,
  private networks, link-local (cloud metadata), carrier-grade NAT,
  multicast, reserved and unspecified ranges, in IPv4 and IPv6, including
  IPv4 written inside IPv6;
* connects to the address it checked (TLS is still verified against the
  host name), so a DNS answer that changes between the check and the
  connection (DNS rebinding) can't send it elsewhere;
* checks every redirect the same way, follows at most 5, and drops
  ``Authorization`` and cookies when a redirect leaves the original site;
* stops after ``timeout`` seconds in total and ``max_bytes`` of body.

Internal services you do mean to call can be allowed with
``PYWEB_FETCH_ALLOW`` (comma-separated host names or networks, e.g.
``hooks.internal,10.2.0.0/16``) or ``fetch(..., allow=[...])``.

Behind an outbound proxy (``HTTPS_PROXY``), the proxy makes the connection:
``fetch`` still refuses addresses it can resolve or that are written as IPs,
and the proxy should enforce your egress rules for the rest.
"""

from __future__ import annotations

import http.client
import ipaddress
import json as _json
import os
import socket
import ssl
import time
import urllib.parse
import urllib.request

MAX_REDIRECTS = 5
SENSITIVE = ("authorization", "cookie", "proxy-authorization")


class FetchError(Exception):
    """The request was refused (address, scheme, size) or failed (network, timeout)."""


class Response:
    def __init__(self, status, headers, body, url):
        self.status, self.headers, self.body, self.url = status, headers, body, url

    @property
    def ok(self):
        return 200 <= self.status < 300

    @property
    def text(self):
        charset = "utf-8"
        for part in self.headers.get("content-type", "").split(";")[1:]:
            k, _, v = part.strip().partition("=")
            if k.lower() == "charset" and v:
                charset = v.strip('"')
        return self.body.decode(charset, "replace")

    def json(self):
        return _json.loads(self.body)

    def __repr__(self):
        return f"<Response {self.status} {self.url}>"


# ------------------------------------------------------------------ addresses

def _allowed_rules(extra):
    rules = [r.strip() for r in os.environ.get("PYWEB_FETCH_ALLOW", "").split(",") if r.strip()]
    rules += list(extra or ())
    hosts, nets = set(), []
    for r in rules:
        try:
            nets.append(ipaddress.ip_network(r, strict=False))
        except ValueError:
            hosts.add(r.lower().rstrip("."))
    return hosts, nets


def public(ip):
    """True when ``ip`` is a public internet address."""
    ip = ipaddress.ip_address(ip)
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.version == 6 and ip.sixtofour is not None:
        ip = ip.sixtofour
    return ip.is_global and not ip.is_multicast


def _check(host, port, allow):
    """The addresses to connect to for ``host`` (all checked), or FetchError."""
    hosts, nets = _allowed_rules(allow)
    name = host.lower().rstrip(".")
    try:
        infos = socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"can't resolve {host}: {exc.strerror or exc}") from None
    addrs = list(dict.fromkeys(info[4][0] for info in infos))
    if name in hosts:
        return addrs
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not public(ip) and not any(ip in n for n in nets):
            raise FetchError(f"{host} resolves to {a}, which isn't a public address (set PYWEB_FETCH_ALLOW "
                             "to allow an internal service)")
    return addrs


def _literal_check(host, allow):
    """With a proxy: refuse IP literals and names that resolve here to internal addresses."""
    hosts, nets = _allowed_rules(allow)
    if host.lower().rstrip(".") in hosts:
        return
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        try:
            ips = {i[4][0] for i in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
        except socket.gaierror:
            return                       # the proxy resolves it
        for a in ips:
            ip = ipaddress.ip_address(a.split("%")[0])
            if not public(ip) and not any(ip in n for n in nets):
                raise FetchError(f"{host} resolves to {a}, which isn't a public address") from None
        return
    if not public(ip) and not any(ip in n for n in nets):
        raise FetchError(f"{host} isn't a public address")


# ------------------------------------------------------------------ connections

class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, addrs, timeout):
        super().__init__(host, port, timeout=timeout)
        self._addrs = addrs

    def connect(self):
        last = None
        for addr in self._addrs:
            try:
                self.sock = socket.create_connection((addr, self.port), self.timeout)
                return
            except OSError as exc:
                last = exc
        raise last or OSError("no address to connect to")


class _PinnedHTTPS(_PinnedHTTP):
    default_port = 443

    def __init__(self, host, port, addrs, timeout, context):
        super().__init__(host, port, addrs, timeout)
        self._context = context

    def connect(self):
        super().connect()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _proxy_for(scheme, host):
    proxies = urllib.request.getproxies()
    if not proxies.get(scheme) or urllib.request.proxy_bypass(host):
        return None
    return urllib.parse.urlsplit(proxies[scheme])


def _context():
    ctx = ssl.create_default_context()
    bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if bundle and os.path.exists(bundle):
        ctx.load_verify_locations(bundle)
    return ctx


def _open(parts, method, headers, body, timeout, allow):
    host, scheme = parts.hostname, parts.scheme
    port = parts.port or (443 if scheme == "https" else 80)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    proxy = _proxy_for(scheme, host)
    if proxy is not None:
        _literal_check(host, allow)
        pport = proxy.port or (443 if proxy.scheme == "https" else 80)
        if scheme == "https":
            conn = http.client.HTTPSConnection(proxy.hostname, pport, timeout=timeout, context=_context())
            conn.set_tunnel(host, port)
        else:
            conn = http.client.HTTPConnection(proxy.hostname, pport, timeout=timeout)
            path = urllib.parse.urlunsplit(parts._replace(fragment=""))
    else:
        addrs = _check(host, port, allow)
        conn = (_PinnedHTTPS(host, port, addrs, timeout, _context()) if scheme == "https"
                else _PinnedHTTP(host, port, addrs, timeout))
    conn.request(method, path, body=body, headers=headers)
    return conn, conn.getresponse()


def fetch(url, *, method="GET", headers=None, data=None, json=None, timeout=10.0, max_bytes=5_000_000,
          allow=None, follow_redirects=True):
    """Request ``url`` and return a :class:`Response`; :class:`FetchError` when refused or failed.

    ``data`` (bytes, str or a dict sent as a form) or ``json`` is the body. ``allow`` adds host
    names or networks to ``PYWEB_FETCH_ALLOW`` for this call.
    """
    headers = {k: v for k, v in (headers or {}).items()}
    body = None
    if json is not None:
        body = _json.dumps(json).encode()
        headers.setdefault("Content-Type", "application/json")
    elif isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode()
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif data is not None:
        body = data.encode() if isinstance(data, str) else bytes(data)
    headers.setdefault("User-Agent", "PyWeb")
    deadline = time.monotonic() + timeout
    origin = None
    for _ in range(MAX_REDIRECTS + 1):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchError(f"only http and https URLs can be fetched (got {url[:80]!r})")
        here = (parts.scheme, parts.hostname, parts.port)
        origin = origin or here
        if here != origin:
            headers = {k: v for k, v in headers.items() if k.lower() not in SENSITIVE}
        left = deadline - time.monotonic()
        if left <= 0:
            raise FetchError(f"timed out after {timeout:g}s")
        try:
            conn, resp = _open(parts, method, headers, body, left, allow)
        except FetchError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise FetchError(f"{parts.hostname}: {exc}") from None
        try:
            if follow_redirects and resp.status in (301, 302, 303, 307, 308) and resp.getheader("Location"):
                url = urllib.parse.urljoin(url, resp.getheader("Location"))
                if resp.status == 303 or (resp.status in (301, 302) and method == "POST"):
                    method, body = "GET", None
                    headers.pop("Content-Type", None)
                continue
            chunks, size = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise FetchError(f"timed out after {timeout:g}s")
                chunk = resp.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError(f"the response is larger than {max_bytes} bytes")
                chunks.append(chunk)
            return Response(resp.status, {k.lower(): v for k, v in resp.getheaders()}, b"".join(chunks), url)
        except (OSError, http.client.HTTPException) as exc:
            raise FetchError(f"{parts.hostname}: {exc}") from None
        finally:
            conn.close()
    raise FetchError(f"more than {MAX_REDIRECTS} redirects")
