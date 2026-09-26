# PyWeb — Python from browser to database

> One language. Every layer.

Write one coherent Python app. PyWeb infers browser/server placement,
lowers plain variables to fine-grained reactive signals (no VDOM), and
generates typed RPC — no manual APIs, no state library, no bundler config.

```python
from pyweb import App

app = App()

@app.page("/")
def Home():
    count = 0

    def increment():
        count += 1

    <main>
        <h1>Counter</h1>
        <button onclick={increment}>
            Count: {count}
        </button>
    </main>
```

`count` is an ordinary variable. The compiler sees it read by markup
and mutated by an event, makes it reactive, and updates only the
button's text node — no virtual DOM, no `useState`, no fetch calls.

## What v1 includes

| Layer | Status |
|---|---|
| Compiler (`.pyweb` → SSR HTML + JS + source maps) | ✅ |
| Fine-grained reactivity (plain vars → signals) | ✅ |
| Auto RPC (`@server` → endpoint + typed stub + trace) | ✅ |
| Browser/server partitioning with reasons (`inspect`) | ✅ |
| Postgres / MySQL / SQLite + pooling + streaming | ✅ |
| Realtime (Redis streams + degrade) + persisted jobs | ✅ |
| Auth (RBAC policies, rotation, WebAuthn ES256) | ✅ |
| Typed browser APIs (`pyweb.browser`) | ✅ |
| Styling (scoped modules, tokens, Tailwind) | ✅ |
| Production build (hashed, minified, split) | ✅ |
| Traces, error codes, time-travel log, DevTools data | ✅ |
| Plugins, platform targets, benchmarks | ✅ |
| Docker / K8s / any VM (no cloud lock-in) | ✅ |

Total JS for counter+todo+blog: **2323 bytes**
(`python -m pyweb.bench`).

## Quickstart

```bash
pip install -e .
python -m pyweb.cli inspect examples/counter/app.pyweb   # placement + RPC
python -m pyweb.cli build examples/counter/app.pyweb --out dist --production
python -m pyweb.cli dev examples/counter/app.pyweb       # http://localhost:8000
python -m pytest tests/ -q
```

## Docs

`docs/00-quickstart.md` → `docs/01-tutorial-todo.md` for beginners;
`02-reactivity` through `07-escape-hatches` for the core model;
`08-browser-apis`, `09-styling`, `10-production-build`,
`11-observability`, `12-production-services`, `13-plugins-platform`
for production v1. `ARCHITECTURE.md` is the full design;
`docs/BUGLOG.md` logs every bug found and fixed.

## Commands

```bash
python -m pyweb.cli new <name>        # scaffold
python -m pyweb.cli dev <file>        # hot-reload dev server
python -m pyweb.cli build <file> --out dist [--production] [--budget f=20KB]
python -m pyweb.cli inspect <file> [--security]  # placement, RPC, findings
python -m pyweb.cli check <file>      # types + security gates
python -m pyweb.cli deploy --target docker|k8s
python -m pyweb.bench                 # bundle/SSR benchmark
```

## Escape hatches

Every abstraction unwraps: components → primitives → raw HTML/CSS →
npm/JS (`pyweb.npm` reads `.d.ts` to typed bindings) → browser APIs;
server side down to ASGI/SQL. `pyweb inspect` shows what the compiler
decided and why — magic you can audit.
