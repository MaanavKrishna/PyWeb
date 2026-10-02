# Changelog

All notable changes to this project are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [semantic versioning](https://semver.org).

## [Unreleased]

### Fixed
- Migrations: the `pyweb_migrations` journal table can now be created on
  MySQL (`VARCHAR(255)` key instead of `TEXT`).

### Removed
- Modules the 1.0 framework no longer uses: `pyweb.components` (pure-Python
  element API), `pyweb.routing` (unused router), `pyweb.reactive`
  (server-side `Signal`/`Computed`/`Effect`/`live`), and
  `pyweb.compiler.placement` (superseded by the compiler's boundary checks).
- The `@browser` and `@shared` decorators, which had no effect.
- `pyweb.compiler.rpc.client_stub`/`server_handler` and unused AST classes.
- `docs/BUGLOG.md` (it described pre-1.0 internals) and the committed
  `website/dist/`; the Pages workflow builds the site.

## [1.0.0]

The first stable release. Earlier development versions were labelled
1.0 but only the simplest counter pattern worked in a browser; this
release rebuilds the compiler, runtime and server around a design that
works end to end, and is verified in a real browser.

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
- The regex-based codegen, the virtual test client that only recorded
  clicks, documentation "snippets" that did not use PyWeb, island and
  streaming-SSR string helpers without runtime support, and the no-op
  `--security-scan` flag.

## [0.1.0]
- Initial prototype.
