"""Test harness: virtual browser over compiled apps (no real browser)."""

from __future__ import annotations

import json
import re


class VirtualPage:
    def __init__(self, client, path, html):
        self.client = client
        self.path = path
        self.html = html

    def text(self, needle):
        hay = re.sub(r"<[^>]+>", " ", self.html)
        return needle in hay

    def click(self, label):
        # Simulate: re-render is server-driven in tests; record the event.
        self.client.events.append(("click", self.path, label))
        return self

    def fill(self, name, value):
        self.client.events.append(("fill", self.path, name, value))
        return self


class Client:
    """Full-stack test client: routing + RPC without sockets."""

    def __init__(self, compiled, rpc_impls=None):
        from .runtime.server import Server
        self.server = Server(compiled)
        for fn in (rpc_impls or {}).values():
            self.server.register_rpc(fn)
        self.events: list = []

    def open(self, path):
        from .runtime.server import Request
        resp = self.server.handle(Request("GET", path))
        assert resp.status == 200, f"GET {path} -> {resp.status}"
        return VirtualPage(self, path, resp.body)

    def rpc(self, name, args=None):
        from .runtime.server import Request
        resp = self.server.handle(Request("POST", f"/__pyweb/rpc/{name}",
                                          body=json.dumps({"args": args or {}}).encode()))
        payload = json.loads(resp.body)
        assert resp.status == 200, f"RPC {name} -> {resp.status}: {payload}"
        return payload["result"]
