# PyWeb Developer Tooling (Track D)

## CLI

```bash
python -m pyweb.cli new hello        # scaffold app/
python -m pyweb.cli dev --port 5173  # static server + watcher + live-reload
python -m pyweb.cli build --out dist --budget runtime.js=8KB --budget 2KB
python -m pyweb.cli inspect [--json] # app graph: routes/components/signals/RPC
python -m pyweb.cli npm types.d.ts -o stubs.py   # .d.ts -> dataclass stubs
```

`test / check / fmt / lint / db / deploy` exist as stubs and exit 0.

## `pyweb dev`

- Static server for `--root` (or `--out`), default port 5173.
- Polling watcher (stdlib `threading`, default 250ms poll / 150ms debounce);
  on change: instant rebuild hook writes `.pyweb/last-build.json`, then the
  browser is notified over SSE (`GET /__pyweb__/reload`).
- Signal-only edits (`count.set(...)` / `.update(...)` hunks) broadcast
  `hot-signal`: the overlay client calls `window.__pywebHotSignal()` and only
  reloads when it returns falsy — a state-preserving reload attempt. All other
  edits broadcast `reload` (full reload).
- Console is cleared on each rebuild with status lines:
  `compiler:` / `server:` / `db:` / `debugger:`.
- Endpoints: `/__pyweb__/overlay.js`, `/__pyweb__/sourcemap?stack=...`,
  `/__pyweb__/status`. HTML responses get a live-reload bootstrap injected.

## `pyweb inspect`

- ASCII tree by default; `--json` emits
  `{routes, components, signals, rpcs, rpc_edges}` where each component carries
  `placement: {decision, reason}`.
- Placement is consumed read-only: if a `placement` module offers
  `decide(graph_dict)`, its decisions/reasons are used; otherwise kind-based
  defaults apply (`ServerComponent` → server, `ClientComponent` → client).
  `placement.py` itself is never modified.

## Error overlay

`/__pyweb__/overlay.js` installs `window.onerror` / `unhandledrejection`
hooks, POSTs stacks to `/__pyweb__/sourcemap`, and renders a full-screen
overlay. The server maps frames through `.pyweb/*.map.json`
(`{"mappings": {"<generated>": ".pyweb:<line>"}}`); with no artifacts it
degrades gracefully to the raw stack (`mapped: false`).

## Bundle budgets

`pyweb build --budget runtime.js=8KB --budget 2KB` fails with exit 2 and a
`budget breach: <label> is <n>B > <m>B` message on stderr. Per-file
(`name=size`) and total (`size`) forms; repeatable.

## Benchmarks

```bash
python3 benchmarks/run.py --out benchmarks/BASELINE.md --check
```

Measures SSR bytes, static-page JS bytes (<2KB budget), runtime bytes
(<8KB budget), local-loopback TTFB, and full-suite time. See
`benchmarks/BASELINE.md` for the committed numbers.

## LSP (`pyweb/lsp.py`)

Pure functions, no server dependency:

- `complete_components / complete_props / complete_routes /`
  `complete_model_fields / complete` over a `DocumentState`
  (build one from an app graph with `state_from_graph`).
- `diagnostics_for_compile_error(err)` maps `CompileError`-shaped spans
  (`file/line/col/end_line/end_col/message`, `span` tuple or dict) to LSP
  `Diagnostic` dicts (0-based lines, `severity: 1`, `source: "pyweb"`).

### VS Code setup

PyWeb has no published extension yet; wire any generic LSP client to a small
shim that imports `pyweb.lsp`:

```jsonc
// .vscode/settings.json
{
  "python.languageServer": "None",
  "pyweb.lsp.trace": true
}
```

Example shim (`pyweb-lsp` on `PATH`):

```python
#!/usr/bin/env python3
"""Minimal stdio LSP shim around pyweb.lsp (completion + diagnostics)."""
import json, sys
from pyweb._graph import discover_app_graph
from pyweb import lsp as L

state = L.state_from_graph(discover_app_graph("."))
for line in sys.stdin:
    try: msg = json.loads(line)
    except ValueError: continue
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params", {})
    if method == "textDocument/completion":
        prefix = params.get("prefix", "")
        items = [i.to_dict() for i in L.complete(state, prefix)]
        sys.stdout.write(json.dumps({"id": mid, "result": items}) + "\n")
    elif method == "textDocument/publishDiagnostics":
        diags = L.diagnostics_for_compile_error(params.get("errors", []))
        sys.stdout.write(json.dumps({"id": mid, "result": diags}) + "\n")
    sys.stdout.flush()
```

Point the client at `pyweb-lsp` with `stdio` transport. `initialize`,
`initialized`, and `shutdown` are no-ops (reply `{"id": mid, "result": null}`).

## npm interop (`pyweb/npm.py`)

Parses `interface` blocks (primitive / `T[]` / `Array<T>` / `?optional` /
string-literal unions / `extends`) into `@dataclass` types with
`from_dict` + `validate_<Name>` raising `TypeError` on missing/invalid data.
Unknown referenced bases get an empty `@dataclass` placeholder so generated
code always imports cleanly.
