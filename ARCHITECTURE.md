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
         runtime/server Server ─► hosting.Site ─► pyweb dev │ pyweb serve │ asgi.create_app
                                                      │
browser:  runtime.js (signals, DOM regions, py helpers, rpc) + <Page>.js ─► mount()
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
  supports retries, timeouts, abort signals and NDJSON streaming.

## 7. Server (`app_loader.py`, `runtime/server`, `hosting.py`)

`LoadedApp` executes the Python half of the file as a module (markup
placeholders are no-ops) and appends, for each page and component, a
*state function*: a copy of the function without markup statements that
returns `locals()`. Rendering a page calls it with the route parameters
(converted by annotation), honours an early `redirect(...)`, renders the
UI tree with `ssr.Renderer` (real Python evaluation, same rules as the
browser), and embeds the browser-read values as JSON.

`Server` routes pages, dispatches RPC (validation by annotation, JSON
and Origin checks, auth/permission gates, rate limiting, timeouts in a
shared pool, async functions, structured errors with request ids and
`traceparent`), and runs everything inside a `contextvars` request
context that powers `request`, `session` and cookies. `hosting.Site`
adds static files, health checks and security headers and is shared by
`pyweb dev`, `pyweb.testing` and the ASGI adapter; `pyweb serve` uses
the same `Server` behind its threaded HTTP handler.

## 8. Build (`build.py`)

Copies the source to `dist/app.pyweb`, writes the runtime and page
modules (rewriting the runtime import to the hashed file name),
token-aware minification in production mode, the static prerender per
page, the user's `static/` folder, `manifest.json` (with raw and gzip
sizes) and a Dockerfile.

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
  scalable, debuggable with curl.
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
| Services | Postgres, MySQL and Redis integration tests (`tests/integration`) |
| Docs | every `pyweb` code block in the docs compiles (`tests/test_docs.py`) |
