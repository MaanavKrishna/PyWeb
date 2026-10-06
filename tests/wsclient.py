"""A tiny WebSocket client for tests and the deploy smoke test (no pytest needed)."""

import base64
import json
import os
import socket
import time

from pyweb.net import ws as W

MASK = b"\x37\xfa\x21\x3d"


class Client:
    def __init__(self, r, *, origin=True, cookie=""):
        self.s = socket.create_connection(("127.0.0.1", r.port), timeout=3)
        key = base64.b64encode(os.urandom(16)).decode()
        lines = ["GET /__pyweb/ws HTTP/1.1", f"Host: 127.0.0.1:{r.port}", "Upgrade: websocket",
                 "Connection: Upgrade", f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
        if origin:
            lines.append(f"Origin: {origin if isinstance(origin, str) else r.url}")
        if cookie:
            lines.append(f"Cookie: {cookie}")
        self.s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            part = self.s.recv(4096)
            if not part:
                break
            buf += part
        self.status = int(buf.split(b" ")[1]) if buf else 0
        self.parser = W.Parser(client=True)
        self.extra = buf.partition(b"\r\n\r\n")[2]
        self.events = []

    def send(self, msg):
        self.s.sendall(W.frame(W.TEXT, json.dumps(msg).encode(), mask=MASK))

    def raw(self, data):
        self.s.sendall(data)

    def next(self, kind=None, timeout=3):
        end = time.time() + timeout
        while time.time() < end:
            while self.events:
                ev = self.events.pop(0)
                if isinstance(ev, W.Message):
                    msg = json.loads(ev.data)
                    if kind is None or msg.get("t") == kind:
                        return msg
                elif isinstance(ev, W.Closed):
                    return ev
            data, self.extra = self.extra, b""
            if not data:
                try:
                    data = self.s.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    return None
            self.events += self.parser.feed(data)
        raise AssertionError(f"no {kind} message")
