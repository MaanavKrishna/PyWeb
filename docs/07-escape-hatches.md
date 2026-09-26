# 07 - Escape hatches

> Sources: `examples/snippets/offline_sync.py` (sync contract +
> `ESCAPE_HATCHES`) and `examples/snippets/chat_realtime.py` (transports).

## Realtime: SSE with polling fallback

<!-- snippet: examples/snippets/chat_realtime.py -->
```python
<ul data-pw-id="chat-log" pw-bind="messages"
    pw-stream="/__pyweb/events" pw-poll="/__pyweb/poll">
</ul>
# strategy: sse-with-poll-fallback
```

<!-- snippet: examples/snippets/chat_realtime.py -->
```python
def pick_transport(sse_available: bool) -> str:
    """Return the transport the client should use."""
    return SSE_PATH if sse_available else POLL_PATH


def parse_sse_frame(frame: str) -> dict:
    """Parse one SSE frame's data line (mirrors the e2e assertion)."""
    import json as _json

    for line in frame.splitlines():
        if line.startswith("data:"):
            return _json.loads(line[len("data:"):].strip())
    raise ValueError("no data line in SSE frame")
```

If the SSE path is absent, the client polls `GET /__pyweb/poll` - the
chat reference app documents both (`examples/chat/meta.json`).

## Offline sync contract

<!-- snippet: examples/snippets/offline_sync.py -->
```python
<ul data-pw-id="notes-list" pw-bind="notes" pw-sync="localStorage pw-outbox">
</ul>
# push: POST /__pyweb/rpc/sync_notes {notes: [...]}
# conflict: last-write-wins
```

<!-- snippet: examples/snippets/offline_sync.py -->
```python
def queue_push(outbox: list, note: dict) -> list:
    """Queue a note locally (mirrors the documented offline contract)."""
    return outbox + [note]


def merge_notes(server: list, outbox: list) -> list:
    """Last-write-wins merge of queued notes onto server state."""
    merged = {n["id"]: n for n in server}
    merged.update({n["id"]: n for n in outbox})
    return [merged[k] for k in sorted(merged)]
```

## Raw escape hatches

<!-- snippet: examples/snippets/offline_sync.py -->
```python
ESCAPE_HATCHES = ("raw-html", "raw-sql", "custom-headers")
```

When the framework can't express something, drop down: raw HTML for
markup, raw SQL for queries, custom headers for responses. Each hatch
is still covered by the same SSR/RPC assertions as framework-native
code.
