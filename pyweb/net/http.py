"""HTTP/1.1 request parsing with no I/O, strict about anything that could desync a proxy.

Request smuggling happens when a proxy and a server disagree about where one
request ends. So anything ambiguous is refused with 400 instead of guessed:

* both ``Content-Length`` and ``Transfer-Encoding``; more than one
  ``Content-Length``; a length that isn't plain digits;
* any ``Transfer-Encoding`` other than exactly ``chunked``;
* folded (``obs-fold``) header lines, whitespace before the colon, control
  characters in values, bare ``LF`` line endings;
* chunk sizes that aren't plain hex (no sign, ``0x``, or absurd length).

Limits: request line 8 KB (414), headers 16 KB / 100 fields (431).
"""

from __future__ import annotations

import re

MAX_LINE = 8 * 1024
MAX_HEAD = 16 * 1024
MAX_FIELDS = 100

_TOKEN = re.compile(rb"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_BAD_VALUE = re.compile(rb"[\x00-\x08\x0a-\x1f\x7f]")
_DIGITS = re.compile(rb"^[0-9]{1,18}$")
_HEX = re.compile(rb"^[0-9A-Fa-f]{1,15}$")
_TARGET = re.compile(rb"^(/[\x21-\x7e]*|\*)$")
REASONS = {
    100: "Continue", 101: "Switching Protocols", 200: "OK", 201: "Created", 202: "Accepted",
    204: "No Content", 206: "Partial Content", 301: "Moved Permanently", 302: "Found", 303: "See Other",
    304: "Not Modified", 307: "Temporary Redirect", 308: "Permanent Redirect", 400: "Bad Request",
    401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
    408: "Request Timeout", 409: "Conflict", 410: "Gone", 411: "Length Required", 413: "Content Too Large",
    414: "URI Too Long", 415: "Unsupported Media Type", 417: "Expectation Failed", 422: "Unprocessable Content",
    426: "Upgrade Required", 429: "Too Many Requests", 431: "Request Header Fields Too Large",
    500: "Internal Server Error", 501: "Not Implemented", 502: "Bad Gateway", 503: "Service Unavailable",
    504: "Gateway Timeout", 505: "HTTP Version Not Supported",
}


class HTTPError(Exception):
    """The request can't be served; answer ``status`` and close the connection."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class RequestHead:
    __slots__ = ("method", "target", "version", "headers", "fields", "keep_alive", "chunked", "length",
                 "expect_continue", "upgrade")

    def __init__(self, method, target, version, fields):
        self.method = method
        self.target = target
        self.version = version
        self.fields = fields                  # [(lower-case name, value)] in order
        self.headers = {}                     # name -> value; repeated fields joined like proxies do
        for name, value in fields:
            if name in self.headers:
                self.headers[name] += ("; " if name == "cookie" else ", ") + value
            else:
                self.headers[name] = value
        conn = {t.strip().lower() for t in self.headers.get("connection", "").split(",")}
        if version == "HTTP/1.1":
            self.keep_alive = "close" not in conn
        else:
            self.keep_alive = "keep-alive" in conn
        self.upgrade = "upgrade" in conn and bool(self.headers.get("upgrade"))
        self.chunked = False
        self.length = 0
        self.expect_continue = self.headers.get("expect", "").lower() == "100-continue"

    def header_dict(self):
        """Headers with ``Title-Case`` names (what the rest of PyWeb expects)."""
        return {"-".join(p.capitalize() for p in k.split("-")): v for k, v in self.headers.items()}


def parse_head(raw: bytes) -> RequestHead:
    """Parse a request line and headers (``raw`` ends with the blank line, CRLF CRLF)."""
    if not raw.endswith(b"\r\n\r\n"):
        raise HTTPError(400, "incomplete request head")
    lines = raw[:-4].split(b"\r\n")
    if any(b"\n" in line or b"\r" in line for line in lines):
        raise HTTPError(400, "bare CR or LF in the request head")
    request_line = lines[0]
    if len(request_line) > MAX_LINE:
        raise HTTPError(414, "request line too long")
    parts = request_line.split(b" ")
    if len(parts) != 3:
        raise HTTPError(400, "malformed request line")
    method, target, version = parts
    if not _TOKEN.match(method):
        raise HTTPError(400, "malformed method")
    if version not in (b"HTTP/1.1", b"HTTP/1.0"):
        if version.startswith(b"HTTP/"):
            raise HTTPError(505, "only HTTP/1.0 and HTTP/1.1 are supported")
        raise HTTPError(400, "malformed request line")
    if not _TARGET.match(target) or (target == b"*" and method != b"OPTIONS"):
        raise HTTPError(400, "the request target must be a path")
    fields = []
    for line in lines[1:]:
        if not line:
            raise HTTPError(400, "empty header line")
        if line[:1] in (b" ", b"\t"):
            raise HTTPError(400, "folded header lines aren't accepted")
        name, sep, value = line.partition(b":")
        if not sep or not _TOKEN.match(name):
            raise HTTPError(400, "malformed header field")       # also catches "Name :"
        value = value.strip(b" \t")
        if _BAD_VALUE.search(value):
            raise HTTPError(400, "control character in a header value")
        fields.append((name.decode("ascii").lower(), value.decode("latin-1")))
    if len(fields) > MAX_FIELDS:
        raise HTTPError(431, "too many header fields")
    head = RequestHead(method.decode("ascii"), target.decode("ascii"), version.decode("ascii"), fields)
    lengths = [v for k, v in fields if k == "content-length"]
    encodings = [v for k, v in fields if k == "transfer-encoding"]
    hosts = [v for k, v in fields if k == "host"]
    if head.version == "HTTP/1.1" and len(hosts) != 1:
        raise HTTPError(400, "exactly one Host header is required")
    if lengths and encodings:
        raise HTTPError(400, "both Content-Length and Transfer-Encoding")
    if len(lengths) > 1:
        raise HTTPError(400, "more than one Content-Length")
    if encodings:
        if head.version != "HTTP/1.1":
            raise HTTPError(400, "Transfer-Encoding needs HTTP/1.1")
        if len(encodings) != 1 or encodings[0].strip().lower() != "chunked":
            raise HTTPError(501, "only Transfer-Encoding: chunked is supported")
        head.chunked = True
    elif lengths:
        if not _DIGITS.match(lengths[0].encode("latin-1")):
            raise HTTPError(400, "malformed Content-Length")
        head.length = int(lengths[0])
    if head.expect_continue is False and "expect" in head.headers:
        raise HTTPError(417, "unsupported Expect")
    return head


class ChunkedDecoder:
    """Decode a chunked body incrementally, refusing more than ``limit`` bytes of data."""

    def __init__(self, limit):
        self.limit = limit
        self.size = 0
        self.buf = bytearray()
        self.state = "size"                    # size -> data -> crlf -> size ... -> trailers -> done
        self.left = 0
        self.trailer_bytes = 0

    @property
    def done(self):
        return self.state == "done"

    def feed(self, data: bytes) -> bytes:
        """Body bytes decoded from ``data``. Raises :class:`HTTPError` on bad framing."""
        self.buf += data
        out = bytearray()
        while True:
            if self.state == "size":
                end = self.buf.find(b"\r\n")
                if end < 0:
                    if len(self.buf) > 1024:
                        raise HTTPError(400, "chunk size line too long")
                    break
                line = bytes(self.buf[:end])
                del self.buf[:end + 2]
                size, _, _ext = line.partition(b";")
                size = size.rstrip(b" \t")
                if not _HEX.match(size):
                    raise HTTPError(400, "malformed chunk size")
                n = int(size, 16)
                if self.size + n > self.limit:
                    raise HTTPError(413, "request body too large")
                if n == 0:
                    self.state = "trailers"
                else:
                    self.left, self.state = n, "data"
            elif self.state == "data":
                if not self.buf:
                    break
                take = min(self.left, len(self.buf))
                out += self.buf[:take]
                del self.buf[:take]
                self.left -= take
                self.size += take
                if self.left == 0:
                    self.state = "crlf"
            elif self.state == "crlf":
                if len(self.buf) < 2:
                    break
                if self.buf[:2] != b"\r\n":
                    raise HTTPError(400, "chunk data not followed by CRLF")
                del self.buf[:2]
                self.state = "size"
            elif self.state == "trailers":
                end = self.buf.find(b"\r\n")
                if end < 0:
                    if len(self.buf) > MAX_HEAD:
                        raise HTTPError(431, "trailers too large")
                    break
                line = bytes(self.buf[:end])
                del self.buf[:end + 2]
                self.trailer_bytes += end + 2
                if self.trailer_bytes > MAX_HEAD:
                    raise HTTPError(431, "trailers too large")
                if not line:
                    self.state = "done"
                elif line[:1] in (b" ", b"\t") or b":" not in line:
                    raise HTTPError(400, "malformed trailer")
            else:
                break
        return bytes(out)

    def leftover(self) -> bytes:
        """Bytes received after the body (the next pipelined request)."""
        rest = bytes(self.buf)
        self.buf.clear()
        return rest


def response_head(status, headers, *, version="HTTP/1.1") -> bytes:
    """Status line and headers. Header names and values are checked for CR/LF (response splitting)."""
    lines = [f"{version} {status} {REASONS.get(status, 'Status')}"]
    for name, value in headers:
        value = str(value)
        if "\r" in name or "\n" in name or "\r" in value or "\n" in value or ":" in name:
            raise ValueError(f"invalid response header {name!r}")
        lines.append(f"{name}: {value}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
