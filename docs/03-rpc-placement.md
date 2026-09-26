# 03 - RPC placement

> Source: `examples/snippets/rpc_client.py` (executed by the suite).

## The wire contract

<!-- snippet: examples/snippets/rpc_client.py -->
```python
POST /__pyweb/rpc/increment  {"count": 1}  -> {"ok": true, "count": 2}
POST /__pyweb/rpc/add_todo     {"title": ""} -> 400 {"ok": false, "error": "..."}
```

## Calling RPC

<!-- snippet: examples/snippets/rpc_client.py -->
```python
def call_rpc(name: str, payload: dict, *, base: str = RPC_BASE) -> dict:
    """POST an RPC call and return the decoded JSON body."""
    req = urllib.request.Request(
        f"{base}/{name}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())
```

## Placement rules

<!-- snippet: examples/snippets/rpc_client.py -->
```python
PLACEMENT_RULES = (
    "ssr-for-reads",
    "rpc-for-mutations",
    "validation-error-contract",
)
```

- reads that must SEO-render go through SSR, never RPC;
- mutations go through `POST /__pyweb/rpc/<name>`;
- validation failures return `400`/`422` with
  `{"ok": false, "error": ...}` - asserted by `rpc_roundtrip(...,
  expect_ok=False)`.
