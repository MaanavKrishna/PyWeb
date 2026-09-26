"""Snippet: RPC client + placement rules used by docs/03-rpc-placement.md."""

SOURCE = """POST /__pyweb/rpc/increment  {"count": 1}  -> {"ok": true, "count": 2}
POST /__pyweb/rpc/add_todo     {"title": ""} -> 400 {"ok": false, "error": "..."}
"""

import json
import urllib.request

RPC_BASE = "http://127.0.0.1:8000/__pyweb/rpc"


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


# Placement rules (also asserted by the e2e suite):
# - reads that must SEO-render go through SSR, not RPC;
# - mutations go through POST /__pyweb/rpc/<name>;
# - validation failures return 400/422 {"ok": false, "error": ...}.
PLACEMENT_RULES = (
    "ssr-for-reads",
    "rpc-for-mutations",
    "validation-error-contract",
)


if __name__ == "__main__":
    print(PLACEMENT_RULES)
