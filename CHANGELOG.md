# Changelog

All notable changes to this project are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [semantic versioning](https://semver.org).

## [Unreleased]

### Added
- **Hydration.** Pages now adopt the server-rendered DOM instead of
  rebuilding it: nodes are kept, focus and text typed before the script
  loads survive, and typed values reach their variables. When the server
  HTML doesn't match, the page falls back to a client render and logs
  a warning. The page root carries `data-pw-mode="hydrated"` or
  `"rendered"`.
- `python -m pyweb.bench --max-ssr-ms N --max-compile-ms N` fails when
  a benchmark exceeds its budget; CI runs it.

### Changed
- Server rendering caches compiled template expressions; rendering a
  page is about twice as fast.
- Generated page modules pass element children as a function and wrap
  every child in a call (`$t`, `$dyn`), so nodes are created in document
  order.

## [0.2.0]

Tools for building PyWeb apps with AI assistants.

### Added
- `pyweb mcp`: a Model Context Protocol server (stdio, standard library
  only) for Claude Code, Cursor, Claude Desktop, VS Code and other MCP
  clients. Tools: `pyweb_guide`, `pyweb_new_app`, `pyweb_check` (errors
  with line numbers and fix hints), `pyweb_inspect`, `pyweb_compiled`,
  `pyweb_render` and `pyweb_call` (session cookies persist between calls);
  resources for the guide and templates; a `build_pyweb_app` prompt.
  Tested against the official MCP Python SDK.
- An AI-oriented guide to writing `.pyweb` apps, shipped in the package.
- `pyweb new --template blank|counter|todo|blog|auth|chat`; new projects
  include `AGENTS.md` and `CLAUDE.md` for coding agents.
- The docs site publishes `llms.txt` and `llms-full.txt`, and has a new
  "AI assistants & MCP" page.

## [0.1.0]

The first public release, published on PyPI as `pyweb-stack`. The
unpublished prototype was labelled "1.0" but only the simplest counter
pattern worked in a browser; this release rebuilds the compiler, runtime
and server around a design that works end to end, and is verified in a
real browser.

### Language and compiler
- `.pyweb` parser with a character scanner: tags and expressions may span
  lines, braces and strings nest inside `{...}`, `elif` chains, HTML
  comments, and exact line numbers (the Python half keeps the source's
  line count; this also fixes crashes on Python 3.10/3.11).
- Python → JavaScript translation of a documented subset with Python
  semantics (truthiness, `==` on containers, negative indexing, floor
  division/modulo, string/list/dict/set methods, Python exceptions).
  Unsupported code is a compile error with `file:line`. Verified
  differentially against CPython.
- State inference: signals (mutated by handlers or bound), computeds,
  constants; initial values from literals, the browser, or the server.
  Mutations (`append`, item assignment, `del`, …) are copy-on-write.
- Components with props, defaults and `children`; `on_mount` hook;
  page titles; `App(title=, stylesheets=, lang=)`.
- `@server` calls from handlers compile to awaited typed RPC; async
  propagates through handler calls.
- Only values browser code reads are serialised; values derived from
  server data are computed on the server. Secret-looking names read by
  browser code are rejected.

### Runtime
- New browser runtime: dependency-tracked signals, cached computeds,
  owned effects with disposal, batching; keyed lists and conditionals in
  marker-bounded regions; two-way binding for text, number, checkbox,
  radio and select; `javascript:` URL blocking; RPC client with typed
  errors.

### Server
- Pages render per request with real server values; `request`,
  `session`, `redirect`, `NotFound`; typed route parameters.
- `@server` functions are registered automatically in `dev`, `serve`,
  tests and ASGI.
- New ASGI adapter: `pyweb.asgi.create_app`.
- RPC: JSON-only and same-origin checks, rate limiting, timeouts that
  actually return on time, async functions, JSON conversion for
  dataclasses/datetimes/Decimals.
- Content-Security-Policy and security headers on HTML responses.

### Tooling
- `pyweb dev`: live reload and an in-browser compile-error overlay.
- `pyweb build` produces a self-contained `dist/` (source, manifest with
  gzip sizes, hashed assets, working Dockerfile); `pyweb serve` runs it.
- Token-aware JS minifier (the previous one altered string literals).
- `pyweb.testing.TestClient` and `pyweb.testing.serve` for tests.
- Clean `file:line: message` compile errors from the CLI.

### Data, auth, jobs
- `?` placeholders work on Postgres and MySQL; lazy connection pools.
- WebAuthn: challenge, origin and sign-count checks; DER signatures; uses
  `cryptography` when installed.
- Redis bus and queue work against real Redis.

### Fixed
- Migrations: the `pyweb_migrations` journal table can now be created on
  MySQL (`VARCHAR(255)` key instead of `TEXT`).
- `pyweb serve` crashed on startup (logger misconfiguration).
- Production builds referenced a hashed runtime the page modules never
  imported.
- Installed packages were missing the browser runtime file.
- `transaction()` did not roll back on Postgres/MySQL pools.
- `sqlite:///file.db` pointed at the filesystem root.
- `RedisBus.since()` duplicated and mis-ordered messages;
  `RedisQueue.drain(timeout=0)` blocked forever.
- Jobs raising `TypeError` ran twice.
- `Query.order_by()` ignored `-field` and did not validate a single field.
- The deploy Dockerfile ran a non-existent `serve --dir` flag.

### Removed
- Modules the framework no longer uses: `pyweb.components` (pure-Python
  element API), `pyweb.routing` (unused router), `pyweb.reactive`
  (server-side `Signal`/`Computed`/`Effect`/`live`), and
  `pyweb.compiler.placement` (superseded by the compiler's boundary checks).
- The `@browser` and `@shared` decorators, which had no effect.
- `pyweb.compiler.rpc.client_stub`/`server_handler` and unused AST classes.
- `docs/BUGLOG.md` (it described the prototype's internals) and the committed
  `website/dist/`; the Pages workflow builds the site.
- The regex-based codegen, the virtual test client that only recorded
  clicks, documentation "snippets" that did not use PyWeb, island and
  streaming-SSR string helpers without runtime support, and the no-op
  `--security-scan` flag.

## Pre-release prototype (unpublished)
- Initial prototype.
