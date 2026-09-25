"""Offline sync: queued mutations, LWW + version conflicts, IndexedDB-shaped log.

The server holds authoritative rows ``{id: (version, data)}``. Each client
keeps an op log while offline; on reconnect the log replays in order:
- ``set`` with a higher version wins (last-writer-wins per field set).
- ``delete`` tombstones a row unless a newer ``set`` arrives after it.
- concurrent ``set``/``set`` on the same version applies the resolver
  (default: server value wins, client op reported as conflict).
"""

from __future__ import annotations

import time
import uuid


class Op:
    def __init__(self, kind, row_id, data=None, *, version=0, actor="client", ts=None):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind  # set | delete
        self.row_id = row_id
        self.data = data or {}
        self.version = version
        self.actor = actor
        self.ts = ts if ts is not None else time.time()

    def to_dict(self):
        return {"id": self.id, "kind": self.kind, "row": self.row_id,
                "data": self.data, "version": self.version, "actor": self.actor}


class SyncServer:
    def __init__(self, resolver=None):
        self.rows: dict[str, tuple[int, dict]] = {}
        self.tombstones: dict[str, int] = {}
        self.resolver = resolver or (lambda server, client: ("server", server))
        self.conflicts: list[dict] = []

    def seed(self, row_id, data, version=1):
        self.rows[row_id] = (version, dict(data))

    def apply(self, op):
        if op.kind == "set":
            cur = self.rows.get(op.row_id)
            tomb_v = self.tombstones.get(op.row_id, -1)
            if cur is None:
                if op.version >= tomb_v:
                    self.rows[op.row_id] = (op.version, dict(op.data))
                    self.tombstones.pop(op.row_id, None)
                    return "applied"
                return "stale"
            cur_v, cur_data = cur
            if op.version > cur_v:
                self.rows[op.row_id] = (op.version, dict(op.data))
                return "applied"
            if op.version == cur_v and op.data != cur_data:
                winner, merged = self.resolver(cur_data, op.data)
                self.rows[op.row_id] = (cur_v + 1, dict(merged))
                self.conflicts.append({"row": op.row_id, "winner": winner,
                                       "server": cur_data, "client": op.data})
                return "conflict"
            return "stale"
        if op.kind == "delete":
            cur = self.rows.get(op.row_id)
            if cur is None:
                return "stale"
            cur_v, _ = cur
            if op.version >= cur_v:
                del self.rows[op.row_id]
                self.tombstones[op.row_id] = op.version
                return "applied"
            return "stale"
        raise ValueError(f"unknown op {op.kind!r}")

    def snapshot(self):
        return {k: {"version": v, "data": d} for k, (v, d) in self.rows.items()}


class SyncClient:
    """Local store with an offline queue; mirrors the IndexedDB side."""

    def __init__(self, server=None):
        self.server = server
        self.online = True
        self.local: dict[str, tuple[int, dict]] = {}
        self.queue: list[Op] = []
        self.applied: list[str] = []

    def set(self, row_id, data, version=1):
        op = Op("set", row_id, data, version=version)
        if self.online and self.server is not None:
            self.applied.append(self.server.apply(op))
            self.local[row_id] = (version, dict(data))
        else:
            self.queue.append(op)
            self.local[row_id] = (version, dict(data))
        return op

    def delete(self, row_id, version=1):
        op = Op("delete", row_id, version=version)
        if self.online and self.server is not None:
            self.applied.append(self.server.apply(op))
            self.local.pop(row_id, None)
        else:
            self.queue.append(op)
            self.local.pop(row_id, None)
        return op

    def reconnect(self):
        results = []
        for op in self.queue:
            results.append(self.server.apply(op) if self.server else "no-server")
        n = len(self.queue)
        self.queue.clear()
        if self.server is not None:
            for row_id, snap in self.server.snapshot().items():
                self.local[row_id] = (snap["version"], dict(snap["data"]))
        return {"replayed": n, "results": results}
