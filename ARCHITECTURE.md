# PyWeb — Architecture

> One language. Every layer. Python from browser to database.

## 1. Vision

Write one coherent Python app. PyWeb decides what runs in browser / server /
edge / worker, generates RPC, transport and runtime, keeps HTML real, typed,
secure and fast. Progressive complexity: trivial apps need almost no
concepts; expert apps can drop to any lower layer.

## 2. Programming model

- Pages: `@app.page("/users/{user_id}") def UserPage(user_id: int)`.
- Components: `@component def Card(...)`; markup optional — pure-Python API
  (`Page(Heading(...))`) always works.
- Normal locals become reactive when read by UI and mutated by events.
  Explicit `Signal / Computed / Resource / Effect` for advanced use.
- `@server def create_user(...)` called like a local function from UI code;
  compiler emits endpoint + typed stub.
- Overrides `@browser @server @edge @worker @shared` are escape hatches.
- Safety rule: uncertain placement goes to the server. Secrets and
  server-only imports never enter browser bundles (compile error).

## 3. Source format (`.pyweb`, `.pyx` accepted)

Superset of Python: plain Python plus JSX-like markup statements inside
component/page bodies. Markup supports `{expr}`, `bind={x}`,
`onclick={fn}`, `for`/`if` control flow, components (`<UserCard/>`).
Lowercase tags compile to real HTML; capitalized tags to components.
Implementation (`compiler/parser.py`): markup lines are replaced with
`__pyweb_ui__` placeholders so CPython `ast` parses all logic exactly
(scoping/types preserved), then markup is parsed with a small tag tokenizer
recording source lines. Pure `.py` files use the component-call API and skip
the markup pass. Why: reuses the CPython grammar (IDE friendly), keeps line
numbers, avoids forking Python. Rejected: full custom grammar, string DSLs.
Long-term: Rust tokenizer, same grammar.

## 4. Reactivity

Primitives: `Signal` (mutable), `Computed` (derived, lazy), `Resource`
(async/server/live data), `Effect` (subscriptions). Auto-lowering: a local is
reactive iff (used in rendered UI or a computed) AND (mutated after init, or
a `bind` target, or flows into a computed). Detection = AST read/write sets
+ UI expression scan (`reactivity.py`). `total = price * quantity` becomes a
computed with edges price,quantity → total → text node. No VDOM: each dynamic
expression binds to its DOM node; updates are O(1) per changed signal.

## 5. Typed IR — PyWeb IR (PIR)

Decision: **C — both**. A typed IR (dataclasses in `compiler/codegen/ir.py`)
with a compact optional textual form (`fn add(a: Int) -> Int`) for
`pyweb inspect` / DevTools. PIR is the compiler target: typed exprs, signals,
components, RPC edges with source spans. Lowered to JS (+WASM/natives later)
and Python (server). Types come from annotations; `Email`, models, `Decimal`
become validated schemas. The textual form is a debugging aid, not a user
language. Alternatives: real user-facing language (rejected: forces users to
learn it), untyped IR (rejected: kills typed RPC + interop).

## 6. No-VDOM renderer

Per-component render code creates real DOM once, subscribes text/attr
bindings to signals, flushes topologically. SSR emits the same tree as an
HTML string; activation attaches signals/events without re-render.

## 7. Placement (`placement.py`)

Symbol/effect/data/security graphs. Ordered rules: (1) DB/secret/server-only
import/auth/fs → server; (2) browser APIs/DOM/events → browser; (3) pure +
reachable from UI → browser; (4) pure + only from server fns → server;
(5) uncertain → server. Every decision records a reason for inspect/DevTools.

## 8. Automatic RPC (`rpc.py`)

`@server`/`@worker`/`@edge` fns get `POST /__pyweb/rpc/<name>` with JSON
`{args, kwargs, trace}`, annotation-derived validation, auth-cookie
propagation, CSRF (same-origin + token), typed client exceptions, trace IDs,
AbortController cancellation, retries only when idempotent. Stubs return
promises; `mutate()` helper gives optimistic UI with rollback. Live queries
share the transport over SSE.

## 9. Browser runtime (`runtime/browser/runtime.js`, ~3KB)

`sig/computed/effect/resource`, `bind_text/bind_attr`, event delegation,
`rpc()`, `mutate()`, router, error-overlay hooks. Static pages omit the
runtime import entirely.

## 10. Server runtime (`runtime/server/`)

Stdlib-first (http.server-compatible, ASGI adapter later): typed routing,
SSR/streaming SSR, RPC dispatcher with schema validation, sessions/cookies,
hashed static assets, structured logs/traces (`X-Request-Id` end to end).

## 11. Routing / Forms / Realtime / Jobs / Cache / Auth / DB

Routing: `@app.page` + typed params, layouts, loading/error boundaries,
metadata/sitemap, per-page `static/server/client/stream`. Forms: single
schema from model annotations, client+server validation, progressive
enhancement. Realtime: `live(Model.where(...))` over WS/SSE + fanout,
long-poll fallback. Jobs: `@task` → reactive job object, pluggable backends.
Cache: `@cache(minutes=5, tags=[...])`, never auto-cache non-idempotent fns.
Auth: sessions/OAuth-OIDC/passkeys/magic-link/MFA, RBAC + policies, auth
context flows into RPC. DB: optional `Model` (Postgres-first, SQLite/MySQL),
migrations, pooling, raw-SQL escape; SQLAlchemy also fine.

## 12. Styling / HTML / a11y / SEO

Real semantic HTML, a11y-baked components/forms, SSR + metadata for SEO.
Styling passes through: plain CSS, modules, Tailwind, tokens, `css={...}`
dicts, `class_` props. Never a closed abstraction.

## 13. JS/TS interop

`from npm import package("chart.js")`; `.d.ts` → typed Python stubs
(`pyweb/npm.py` planned); web components, browser APIs, optional React-compat
island. Core never depends on React.

## 14. Offline / optimistic

`@model(sync=True)` → sync engine (server DB ↔ IndexedDB/SQLite ↔ UI):
queued mutations, reconnect sync, configurable conflicts; safe mutations
auto-optimistic with rollback.

## 15. Security

Auto-escaped HTML, parameterized queries, CSRF, secure cookies, secret-flow
analysis (server-secret → browser = compile error), taint checks
(XSS/SQLi/SSRF/traversal/injection), advisory hook. AuthZ always re-checked
server-side.

## 16. Observability / errors / time-travel

Structured logs + traces + metrics, one trace click → RPC → DB → DOM. Errors
map generated frames to `.pyweb` lines with data-flow context. Signal/RPC
event log powers DevTools time-travel inspection/replay (replay = later).

## 17. Dev UX

`pyweb new/dev/build/test/check/fmt/lint/db/inspect/deploy`. `dev`: compiler
+ server + hot reload + error overlay + inspector. `build`: tree-shaken
bundles, hashed assets, route splitting, source maps,
`dist/{server,static,workers,migrations,manifest}`. Tests: component/state/
RPC/virtual-browser/real-browser/DB/e2e. LSP later (completion, boundary
visualization, go-to-def).

## 18. Packages / deploy / no lock-in

`pip install pyweb`; split into `pyweb-compiler/-runtime/-db/-auth/...`
later; Rust hot paths later, no premature rewrite. `pyweb deploy` optional;
`pyweb build && docker build .` anywhere. Standards preserved: Postgres is
Postgres, HTML is HTML.

## 19. Performance targets

Static ≈ zero runtime; counter ≈ <5KB JS; O(changed nodes) updates; streaming
SSR; route-split. Benchmark vs React/Next/Svelte/Solid/Django/FastAPI/Reflex
on the 10 reference apps (counter → offline notes).

## 20. Roadmap

P1 static → P2 exprs → P3 local reactivity → P4 components → P5 RPC →
P6 placement → P7 models → P8 SSR/hydration → P9 dev server → P10 build
(this prototype implements P1–P10 minimally for counter + RPC). Then: live
queries, offline, jobs, DevTools, LSP, mobile/desktop, AI assistant on the
app graph. Hypotheses H1–H6 map to P3 / P6 / P5 / P3+perf / interop / MVP.
