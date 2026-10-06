# Architecture

This document describes how PyWeb is built, for contributors and for
anyone who wants to know exactly what happens to their code.

```text
app.pyweb
   │
   ▼
parser.py ──── split_sources ──► Python AST (CPython)   +   UI tree (markup)
   │                               │                            │
   ▼                               ▼                            ▼
lower.py:  scan_module ─► ModuleContext (server fns, helpers, constants, components, server-only names)
           classify    ─► per page/component: signal / computed / const × literal / browser / server
           Emitter     ─► page JS module  ◄── pyjs.py (Python → JS translation)
   │
   ▼
pipeline.compile_source ─► {pages: {html (static prerender), js, signals, placement, ...}, rpc, graph}
   │
   ├─► build.py      dist/: app.pyweb, manifest.json, hashed static/, Dockerfile
   └─► app_loader.py LoadedApp: exec Python half, register @server fns, render pages per request
                          │
                          ▼
         runtime/server Server ─► hosting.Site ─► net/server (pyweb dev, pyweb serve) │ asgi.create_app
                                    │
          models + db (queries, migrations) · forms · authkit · livedata · jobs · telemetry
                                                      │
browser:  runtime.js (signals, DOM regions, py helpers, rpc) + <Page>.js ─► mount()
          + forms.js, live.js, auth.js, markdown.js on the pages that need them
```

## 1. Parsing (`pyweb/compiler/parser.py`)

A `.pyweb` file is Python plus markup statements. `split_sources`
classifies each physical line as Python, a tag line, a content line
inside an open tag, or a `for`/`if`/`elif`/`else` line that governs
markup. Markup lines become `__pyweb_ui__(<line>)` placeholder calls (or
one-line compound statements for control lines), **keeping the exact
line count**, so CPython parses the logic and every reported line number
is a real `.pyweb` line. Tags and `{expressions}` may span lines; the
continuation lines become blank.

The markup itself is tokenised by a character scanner that understands
balanced brackets and Python string literals inside `{...}`, so
`{len({"a": 1})}` and `attr={lambda: f("}")}` are single expressions.
`parse_ui_block` builds the UI tree (`Element`, `TextNode`, `ExprNode`,
`ControlFor`, `ControlIf` with `elif` chains). Errors are
`PyWebSyntaxError` with the line.

## 2. Module analysis (`lower.scan_module`)

Top-level statements are sorted into: pages (`@app.page`), `@server`
functions (also `@worker`/`@edge`/`@task`), components (`@component` or
capitalised functions with markup), helpers (undecorated functions),
literal constants, and server-only names (imports, classes, non-literal
module values). `pyweb.browser` imports map to JavaScript globals.

`from widgets import Card` where `widgets.pyweb` sits next to the file is
a *library import*: the pipeline compiles that file first (cached per
build, cycles rejected) and binds its components, `@server` functions
and constants into the importer's context. The emitter writes each
library's components into its own scope:
`const { Card } = (() => { ...constants, helpers, components...; return { Card }; })();`,
so files never see each other's names.

## 3. Classification (`lower.classify`)

For each page and component, local assignments are collected in order.
Each name gets:

- a **kind**: `signal` if any handler assigns or mutates it (including
  `.append()`, item assignment, `del`), or it is a `bind` target;
  `computed` if its initializer reads reactive names; otherwise `const`;
- an **origin**: `literal` (via `ast.literal_eval`), `browser` (the
  initializer translates to JavaScript and doesn't load data), or
  `server` (anything else: server/helper calls, imports, page-level
  logic, or derived from another server value).

The set of names browser code reads (markup, handlers, and the inputs of
browser-computed values) decides what is serialised into page state.
Secret-looking names in that set are a compile error.

## 4. Python → JavaScript (`pyweb/compiler/pyjs.py`)

An AST visitor translates expressions and statements of the supported
subset. Name resolution goes through a `Scope` chain whose kinds decide
emission: signals/computeds/props read as `x()` and write as `x(v)`;
`@server` calls become `(await $rpc("name", {param: value}))` and mark
the enclosing function `async` (propagated through handler calls by a
fixpoint pre-pass); helpers are translated on demand and cached. Python
semantics that differ from JavaScript go through `$py` helpers
(`truth`, `eq`, `contains`, `at`, `slice`, `add`, `mul`, `mod`,
`floordiv`, `m` for method calls, …). Mutations rooted at a signal
compile to copy-on-write path updates (`$py.mut`, `$py.setp`,
`$py.delp`). Unsupported constructs raise `CompileError`.

## 5. Code generation (`lower.Emitter`)

Each page becomes one ES module: the runtime import, used module
constants, used helpers, used components, and the page function, which
creates signals/computeds (initial values from the page state JSON with
literal fallbacks), defines handlers, and returns the UI as nested
`$h(tag, props, () => children)` calls. Children are a thunk and every
child is a call (`$t("text")`, `$dyn(() => expr)`, `$h`, `$list`,
`$when`, a component), so nodes are created in document order, which is
what hydration needs. Reactive props are thunks; `for` becomes
`$list(items, row)`, `if` becomes `$when(test, yes, no)`, components
become calls with getter props. Pages with no signals,
handlers or events get no module.

## 6. Browser runtime (`pyweb/runtime/browser/runtime.js`)

- **Reactivity:** push-pull signals with automatic dependency tracking,
  lazy cached computeds, effects with ownership (effects created during
  another effect or `root()` are disposed with it), batching, and
  equality short-circuiting.
- **DOM:** `h()` creates elements (SVG aware) and binds reactive
  props/children with one effect each. Dynamic regions (`dyn`, `list`,
  `when`) live between comment markers so nested regions can change
  shape while the enclosing row or branch can still move or remove them
  as a unit. `list` is keyed by item identity (with a stable composite
  key for tuples) and reuses DOM for unchanged items. `bind()` handles
  text, number, checkbox, radio and select controls and registers before
  other listeners.
- **Hydration:** `mount()` first tries to adopt the server-rendered DOM.
  While hydrating, `h()` claims the next server element instead of
  creating one, `t()`/`dyn()` claim the next text node (splitting text
  the HTML parser merged), and regions insert their comment markers in
  place. Values typed before hydration are kept and pushed into their
  signals afterwards. Any mismatch throws inside the hydration pass,
  which disposes what it built and falls back to a fresh client render.
- **Safety:** text is always set as text; `javascript:` URLs are
  neutralised.
- **RPC:** `rpc()` posts JSON, maps errors to `RPCError(code, message)`,
  supports retries, timeouts and abort signals. Calls to server functions
  that `yield` return an `RpcStream`: an async iterable that reads the
  NDJSON response as it arrives and aborts the request on `cancel()` or
  when the loop is left.
- **Pages, layouts and navigation:** each page and layout is a reactive
  root mounted into its `[data-pw-root]` element; a layout renders a
  `slot()` element that holds the page. Same-origin link clicks fetch the
  next page's HTML, keep the leading layouts whose fingerprint
  (`data-pw-layout`) matches, dispose the roots below them, swap in the
  new HTML and mount the new modules (imported with `import()`; mounting
  is deferred through a registry on `window`, so a re-imported module
  still mounts). Anything unexpected falls back to a full load.
- **Live data:** `live(sig, meta)` subscribes a page variable to its
  query's feed; `watch(get, fn)` runs a handler when a value changes.
  `markdown.js` (loaded only by pages that use `<Markdown>`) renders
  Markdown with the same rules as `pyweb/markdown.py`.

## 7. Server (`app_loader.py`, `runtime/server`, `hosting.py`)

`LoadedApp` executes the Python half of the file as a module (markup
placeholders are no-ops) and appends, for each page and component, a
*state function*: a copy of the function without markup statements that
returns `locals()`. Rendering a page calls it with the route parameters
(converted by annotation), honours an early `redirect(...)`, renders the
UI tree with `ssr.Renderer` (real Python evaluation, same rules as the
browser), and embeds the browser-read values as JSON. Imported `.pyweb`
files are executed the same way as modules registered under their
import name; a component's render env carries the file it came from, so
nested components resolve in that file and its markup sees its globals.

`Server` routes pages, dispatches RPC (validation by annotation and
`Field` rules, JSON and Origin checks, `login=`/`roles=` gates, rate
limiting, timeouts in a shared pool, async functions, structured errors),
posts forms, and runs everything inside a `contextvars` request context
that powers `request`, `session`, cookies and the request's database
transaction. `hosting.Site` adds static files, health checks, metrics,
security headers (with a hashed Content-Security-Policy) and telemetry,
and is shared by `pyweb dev`, `pyweb serve`, `pyweb.testing` and the ASGI
adapter.

**The network server** (`pyweb/net/`) is PyWeb's own, standard library
only: one asyncio loop per process holds every keep-alive connection,
event stream and WebSocket as a coroutine, while page renders and RPC
calls (ordinary blocking Python) run on a thread pool. `http.py` and
`ws.py` are I/O-free parsers: the HTTP parser refuses anything that could
desync a proxy (both `Content-Length` and `Transfer-Encoding`, duplicate
lengths, folded headers, bad chunk sizes) and the WebSocket codec enforces
RFC 6455 masking, control-frame and message-size rules before reading a
payload. `pyweb serve --workers N` starts N processes, each with its own
`SO_REUSEPORT` socket, and drains them gracefully on shutdown. The ASGI
adapter (`asgi.py`) remains for uvicorn, hypercorn and friends.

Streaming server functions return an `RPCStream` body that steps the
generator inside the request's context and closes it when the client
disconnects.

## 8. Data (`models/`, `db/`, `rules.py`, `forms.py`)

- **Databases** (`db/`): one driver layer for SQLite, Postgres and MySQL
  with pooling, request-scoped transactions with savepoints, read
  replicas (read-your-writes inside a request) and `after_commit` hooks.
  `dialect.py` holds the few places the three differ; values are always
  bound parameters.
- **Models** (`models/`): annotated classes become tables. `fields.py`
  holds each column's type, schema and rules in one object; `query.py`
  is an immutable `QuerySet` (`where`, `order`, `include`, `page`,
  aggregates) whose column names come from the Model; `relations.py`
  loads foreign keys and many-to-many with one `IN (...)` query per
  relation and, while developing, raises on a relation read that wasn't
  `include()`d (no silent N+1). Row policies (`Model.policy(read=,
  write=)`) are added to every query and save made for a request.
- **Migrations** (`db/schema.py`, `db/migrate.py`): `from_models` and
  `introspect` describe the wanted and the actual schema the same way;
  `diff` turns the difference into operations, splitting risky changes
  into expand and contract steps. Migrations run under a lock (Postgres
  advisory lock, MySQL `GET_LOCK`, or a row in SQLite) so many servers
  can start at once.
- **Rules and forms**: one `Rules` object checks a value when a Model is
  saved, when an RPC argument arrives and when a form is posted, and its
  JSON schema drives `forms.js` in the browser. `<Form action={fn}>`
  takes its fields from the function's parameters (or the Model it
  edits), adds a CSRF token and a one-time key so a double submit runs
  once, and works as a plain POST without JavaScript.

## 9. Live data (`livedata.py`, `net/live.py`, `runtime/browser/live.js`)

Writes through `pyweb.db` announce their table on the realtime bus after
the transaction commits (a rollback announces nothing). Each distinct
live query (`Post.query().live()` or `live(db, sql)`) is registered once
per process; a watcher re-runs the queries whose tables changed, once for
all viewers, and publishes a **patch** keyed by primary key (`{version,
prev, ops}`: deletes, updates, inserts at an index, moves). One changed
row costs one row on the wire, and `live.js` updates only those rows'
DOM. A browser that missed a version asks for the whole result again.

Each tab opens one WebSocket (`/__pyweb/ws`) and multiplexes every
subscription over it; where WebSockets are blocked it falls back to
Server-Sent Events, then polling. Sockets check `Origin`, re-check the
session every minute (a revoked session is closed), and have bounded
send queues: a client that can't keep up gets one resync instead of an
ever-growing backlog. `rooms.py` adds presence and ephemeral broadcasts.
With `RedisBus`, changes reach every process and host.

## 10. Accounts, jobs and telemetry

- **Auth kit** (`authkit/`): `app.use_auth()` adds Models (users,
  identities, passkeys, tokens, audit events), server-rendered pages that
  work without JavaScript, scrypt or argon2id passwords, passkeys
  (`webauthn.py`, pure Python ES256/RS256), magic links, OAuth with PKCE,
  TOTP, delay-based throttling instead of lockouts, and `/admin` for every
  Model.
- **Jobs** (`jobs/`): `@app.job` and `@app.cron` share one store
  interface with three backends: the app's database (`pyweb_jobs`; claims
  with `FOR UPDATE SKIP LOCKED`, enqueueing joins the request's
  transaction), Redis (sorted sets and Lua scripts) and memory. Leases
  hand a crashed worker's job to another; cron slots are deduplicated by
  key, so every server can run the scheduler and each slot still runs
  once.
- **Telemetry** (`telemetry/`): each request and job gets a
  `RequestTelemetry` (request id = W3C trace id, route, user, queries,
  spans). Logs, Prometheus metrics (merged across worker processes),
  OpenTelemetry spans, error reports and the dev toolbar all read it; a
  job queued in a request continues the request's trace.
- **Deploy** (`deploy/`): `plan.py` reads the app (database? jobs? live
  updates? sign-in?) and decides the services and processes; `targets.py`
  writes the same multi-stage image and web/worker/release processes for
  Docker, Compose, Kubernetes, Fly, Render and Railway.

## 11. Build (`build.py`)

Copies the source to `dist/app.pyweb`, writes the runtime and page
modules (rewriting the runtime import to the hashed file name),
token-aware minification in production mode, the static prerender per
page, the user's `static/` folder, imported `.pyweb` files,
`manifest.json` (with raw and gzip sizes) and a Dockerfile. Layout
modules and `markdown.js` are written the same way; npm packages are
already in `static/vendor/` (put there by `pyweb add`, see
`packages.py`: a registry client, semver resolution, and a crawler that
follows imports from the browser entry point, applies the `browser`
field and records everything in `pyweb.lock`).

## 12. Tools around the compiler

- **`pyweb lsp`** (`lsp.py`): a stdio Language Server. Every keystroke
  compiles the buffer with its real path (so imports resolve); errors and
  `security.check_source` findings become diagnostics, the placement
  report answers hover, and the context's component table drives
  completion and go to definition. Data features read the Models
  statically (`lsp_data.py`: field and query completion, form fields, N+1
  loops) and run `pyweb db check` in the background for the "needs a
  migration" code lens. `editors/vscode` adds a TextMate grammar,
  commands and snippets, and starts the server.
- **`pyweb mcp`** (`mcp.py`): the same compiler, `TestClient`, Playwright
  and pytest behind MCP tools for AI assistants, plus read-only data
  tools (schema, guarded queries, migrations, jobs, recent requests).
- **`pyweb upgrade`** (`upgrade.py`): reads an app and its scripts and
  lists what changed since 0.4, with safe rewrites behind `--fix`.
- **Playground** (`website/playground`): PyWeb itself runs in Pyodide.
  `host.py` wraps a `TestClient`; the page renders its HTML into an
  iframe and a fetch bridge sends the iframe's `/__pyweb/` requests back
  to Python, so pages, RPC, forms, sessions, Models (on Pyodide's SQLite)
  and live updates behave as under `pyweb dev`. Jobs run inline after
  each request, since Pyodide has no threads.

## Design decisions

- **Compile, don't interpret.** Shipping a Python runtime costs megabytes
  and seconds; server-driven UIs cost a round trip per interaction. A
  compiler that targets small JavaScript keeps both costs near zero at
  the price of supporting a subset of Python in the browser, which is
  made explicit by compile errors.
- **Reuse CPython's parser.** The Python half is parsed by `ast`, so
  every Python construct, error and line number on the server side is
  exactly Python's.
- **Infer state from usage.** Developers write variables; the compiler
  decides what is reactive and why, and `pyweb inspect` makes the
  decisions visible.
- **Ship only what the browser reads.** Server values are computed on the
  server and only the browser-read ones are serialised, which is both a
  size and a privacy property.
- **Stateless HTTP everywhere.** Pages per request, RPC as JSON POST,
  sessions as signed cookies: operationally boring, horizontally
  scalable, debuggable with curl. The only long-lived connection is the
  live-data WebSocket, and it holds no state a reconnect can't rebuild.
- **The database is the source of truth.** Jobs, schedules, migrations'
  journal and live-query versions live in tables or derive from them, so
  a crash or a deploy loses nothing and needs no extra service until
  scale asks for Redis.
- **Hydrate, but never trust it blindly.** The client adopts the server
  DOM when it matches exactly and otherwise renders from scratch, so a
  mismatch costs a re-render, never a broken page.

## Testing strategy

| Layer | Tests |
|---|---|
| Parser | unit tests incl. line-number stability (`tests/test_parser_v1.py`) |
| Translator | differential tests against CPython in Node (`tests/test_pyjs_semantics.py`) |
| Runtime | reactive core in Node, DOM behaviour in Chromium (`tests/test_runtime_signals.py`, `tests/e2e/test_runtime_dom.py`) |
| Compiler/server | `TestClient`-based end-to-end tests (`tests/test_e2e.py`) and contract tests |
| Examples | every example app driven in Chromium under the production CSP (`tests/e2e/test_examples_browser.py`) |
| Data | query SQL per dialect, relations, transactions (`tests/test_models_db.py`, `tests/test_migrations_v5.py`), and a Hypothesis property test that random schema changes migrate up and down (`tests/test_migrations_property.py`) |
| Network | HTTP parser and WebSocket codec fuzzed with Hypothesis (`tests/test_net.py`); random writes, then the applied patches must equal a fresh query (`tests/test_live_patches.py`) |
| Jobs | one contract suite for every backend, concurrent workers, a worker killed mid-job (`tests/test_jobs_v5.py`, `tests/test_jobs_app.py`), cron against a brute-force minute scan in DST zones (`tests/test_cron.py`) |
| Services | the full non-browser suite against real Postgres, MySQL and Redis (`tests/integration` and CI) |
| Deploy | `pyweb deploy compose` stack (2 web replicas, worker, Postgres, Redis, Caddy) booted and smoke-tested in CI |
| Editor | the VS Code extension in a real VS Code against the real language server (`editors/vscode/test/integration`) |
| Playground | the playground on Pyodide in Chromium, database apps included (`tests/e2e/test_playground.py`) |
| Docs | every `pyweb` code block in the docs compiles (`tests/test_docs.py`) |
