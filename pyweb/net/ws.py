"""WebSocket protocol (RFC 6455), with no I/O: bytes in, events and bytes out.

Used by PyWeb's own server and the ASGI adapter. Strict where it matters:

* client frames must be masked, reserved bits clear (no extensions);
* control frames are at most 125 bytes and never fragmented;
* a message (all its fragments) is at most ``max_message`` bytes, checked
  from the frame header before the payload is buffered;
* text must be valid UTF-8 (also across fragments), close codes valid.

A protocol error answers with the right close code (1002, 1007, 1009) and
the connection then ends.
"""

from __future__ import annotations

import base64
import codecs
import hashlib
import struct

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

CONT, TEXT, BINARY, CLOSE, PING, PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA

NORMAL = 1000
GOING_AWAY = 1001
PROTOCOL_ERROR = 1002
UNSUPPORTED = 1003
BAD_DATA = 1007
POLICY = 1008
TOO_BIG = 1009
INTERNAL = 1011

# Codes a peer may send (RFC 6455 7.4; 3000-4999 are for libraries and apps).
_VALID_CLOSE = {1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010, 1011, 1012, 1013, 1014}


def accept_key(key: str) -> str:
    """The ``Sec-WebSocket-Accept`` value for a client's ``Sec-WebSocket-Key``."""
    return base64.b64encode(hashlib.sha1(key.encode() + GUID, usedforsecurity=False).digest()).decode()  # noqa: S324 - the RFC's choice


def handshake_problem(method, headers):
    """Why a request isn't a valid WebSocket upgrade (a phrase), or None.

    ``headers`` maps lower-case names to values.
    """
    if method != "GET":
        return "WebSocket upgrades must be GET"
    if "websocket" not in headers.get("upgrade", "").lower():
        return "missing Upgrade: websocket"
    if "upgrade" not in [t.strip().lower() for t in headers.get("connection", "").split(",")]:
        return "missing Connection: Upgrade"
    if headers.get("sec-websocket-version", "") != "13":
        return "unsupported Sec-WebSocket-Version (13 only)"
    key = headers.get("sec-websocket-key", "")
    try:
        if len(base64.b64decode(key, validate=True)) != 16:
            return "bad Sec-WebSocket-Key"
    except ValueError:
        return "bad Sec-WebSocket-Key"
    return None


def frame(opcode: int, payload: bytes = b"", *, fin: bool = True, mask: bytes | None = None) -> bytes:
    """One frame. Servers send unmasked frames; pass ``mask`` (4 bytes) to act as a client (tests)."""
    head = bytes([(0x80 if fin else 0) | opcode])
    n = len(payload)
    bit = 0x80 if mask else 0
    if n < 126:
        head += bytes([bit | n])
    elif n < 1 << 16:
        head += bytes([bit | 126]) + struct.pack(">H", n)
    else:
        head += bytes([bit | 127]) + struct.pack(">Q", n)
    if mask:
        return head + mask + _xor(payload, mask)
    return head + payload


def close_frame(code: int = NORMAL, reason: str = "") -> bytes:
    return frame(CLOSE, struct.pack(">H", code) + reason.encode()[:123])


def _xor(data: bytes, mask: bytes) -> bytes:
    if not data:
        return b""
    n = len(data)
    key = int.from_bytes((mask * (n // 4 + 1))[:n], "big")
    return (int.from_bytes(data, "big") ^ key).to_bytes(n, "big")


class ProtocolError(Exception):
    def __init__(self, code, reason):
        super().__init__(reason)
        self.code = code
        self.reason = reason


class Message:
    """A complete text (``str``) or binary (``bytes``) message."""

    __slots__ = ("data",)

    def __init__(self, data):
        self.data = data

    def __repr__(self):
        return f"Message({self.data!r})"


class Ping:
    __slots__ = ("data",)

    def __init__(self, data):
        self.data = data


class Pong:
    __slots__ = ("data",)

    def __init__(self, data):
        self.data = data


class Closed:
    """The peer sent a close frame (``code`` 1005 when it carried none)."""

    __slots__ = ("code", "reason")

    def __init__(self, code, reason=""):
        self.code = code
        self.reason = reason


class Parser:
    """Feed received bytes; get events. ``client=True`` parses frames sent by a server (tests)."""

    def __init__(self, *, max_message=1 << 20, client=False):
        self.max_message = max_message
        self.client = client
        self.buf = bytearray()
        self.parts: list[bytes] = []
        self.size = 0
        self.kind = None                       # opcode of the message being assembled
        self.utf8 = None
        self.closed = False

    def feed(self, data: bytes):
        """Events for the bytes received so far. Raises :class:`ProtocolError` on bad input."""
        self.buf += data
        out = []
        while not self.closed:
            event = self._next()
            if event is None:
                break
            if event is not True:
                out.append(event)
        return out

    def _next(self):
        buf = self.buf
        if len(buf) < 2:
            return None
        b0, b1 = buf[0], buf[1]
        fin, opcode, masked, n = b0 & 0x80, b0 & 0x0F, b1 & 0x80, b1 & 0x7F
        if b0 & 0x70:
            raise ProtocolError(PROTOCOL_ERROR, "reserved bits set (no extensions were agreed)")
        if not self.client and not masked:
            raise ProtocolError(PROTOCOL_ERROR, "client frames must be masked")
        if self.client and masked:
            raise ProtocolError(PROTOCOL_ERROR, "server frames must not be masked")
        pos = 2
        if n == 126:
            if len(buf) < 4:
                return None
            n = struct.unpack_from(">H", buf, 2)[0]
            pos = 4
            if n < 126:
                raise ProtocolError(PROTOCOL_ERROR, "length not minimally encoded")
        elif n == 127:
            if len(buf) < 10:
                return None
            n = struct.unpack_from(">Q", buf, 2)[0]
            pos = 10
            if n >> 63:
                raise ProtocolError(PROTOCOL_ERROR, "length has the top bit set")
            if n < 1 << 16:
                raise ProtocolError(PROTOCOL_ERROR, "length not minimally encoded")
        control = opcode >= 0x8
        if control:
            if opcode not in (CLOSE, PING, PONG):
                raise ProtocolError(PROTOCOL_ERROR, f"unknown control opcode {opcode}")
            if not fin:
                raise ProtocolError(PROTOCOL_ERROR, "control frames can't be fragmented")
            if n > 125:
                raise ProtocolError(PROTOCOL_ERROR, "control frame too long")
        else:
            if opcode not in (CONT, TEXT, BINARY):
                raise ProtocolError(PROTOCOL_ERROR, f"unknown data opcode {opcode}")
            if opcode == CONT and self.kind is None:
                raise ProtocolError(PROTOCOL_ERROR, "continuation without a message")
            if opcode != CONT and self.kind is not None:
                raise ProtocolError(PROTOCOL_ERROR, "new message before the last one finished")
            if self.size + n > self.max_message:
                raise ProtocolError(TOO_BIG, f"message larger than {self.max_message} bytes")
        mask_len = 4 if masked else 0
        end = pos + mask_len + n
        if len(buf) < end:
            return None
        payload = bytes(buf[pos + mask_len:end])
        if masked:
            payload = _xor(payload, bytes(buf[pos:pos + 4]))
        del buf[:end]
        if control:
            return self._control(opcode, payload)
        if opcode != CONT:
            self.kind = opcode
            self.parts, self.size = [], 0
            self.utf8 = codecs.getincrementaldecoder("utf-8")("strict") if opcode == TEXT else None
        self.parts.append(payload)
        self.size += n
        if self.utf8 is not None:
            try:
                self.utf8.decode(payload, final=bool(fin))   # fail fast on bad UTF-8 mid-message
            except UnicodeDecodeError:
                raise ProtocolError(BAD_DATA, "text message isn't valid UTF-8") from None
        if not fin:
            return True
        data = b"".join(self.parts)
        kind = self.kind
        self.kind, self.parts, self.size, self.utf8 = None, [], 0, None
        return Message(data.decode("utf-8") if kind == TEXT else data)

    def _control(self, opcode, payload):
        if opcode == PING:
            return Ping(payload)
        if opcode == PONG:
            return Pong(payload)
        self.closed = True
        if not payload:
            return Closed(1005)
        if len(payload) == 1:
            raise ProtocolError(PROTOCOL_ERROR, "close frame with a 1-byte body")
        code = struct.unpack(">H", payload[:2])[0]
        if code not in _VALID_CLOSE and not 3000 <= code <= 4999:
            raise ProtocolError(PROTOCOL_ERROR, f"invalid close code {code}")
        try:
            reason = payload[2:].decode("utf-8")
        except UnicodeDecodeError:
            raise ProtocolError(BAD_DATA, "close reason isn't valid UTF-8") from None
        return Closed(code, reason)
