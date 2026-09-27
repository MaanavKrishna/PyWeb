# PyWeb Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

## [1.0.0] — production-grade v1

### Compiler & reactivity
- `.pyweb` syntax specified (`docs/14-syntax.md`): Python + markup line
  rules, `{expr}` holes, control-over-markup, pure-Python alternative.
- Plain locals lower to fine-grained signals (no VDOM); computed values
  form a dependency graph; only bound DOM nodes update.
- Placement inference (browser/server/edge/worker/shared) with security
  boundary graph; `@server` etc. as overrides; `pyweb inspect` explains
  every decision.

### RPC & transport
- Typed RPC: endpoint + stub + serialization + validation + auth
  propagation + CSRF + retries + timeouts + W3C tracing, all generated
  from annotations. `serve` forwards `X-Request-Id`/`traceparent`
  end to end; incoming `traceparent` propagates to logs.
- Rate limiting on by default in `serve` (120/min/IP; `False` disables
  for tests).

### Data
- Postgres/MySQL/SQLite via parameterized-only queries, pooling,
  prepared statements, streaming cursors, retries, pagination.
- Versioned migrations with journal + idempotent apply + status +
  rollback (`pyweb db migrate|status|new`); orphan `.down.sql` rejected.
- Live queries (SSE/WebSocket/polling), Redis jobs bus with retries and
  progress, memory+Redis cache with tags and SWR, offline queue with
  LWW + tombstones, optimistic UI with rollback.

### Auth & security
- Signed sessions (HttpOnly, SameSite=Lax, `Secure` opt-in for prod),
  PBKDF2 passwords, RBAC, rotation, magic links, TOTP, OAuth/OIDC,
  WebAuthn; `pyweb check` gates CI on secret-leak findings.

### Build, serve, deploy
- `pyweb build --production`: hashed assets, minified split bundles,
  extracted CSS, importmap for npm, manifest. Budgets enforced in CI
  on shipped bytes (~10 KB/app incl. shared runtime).
- `pyweb serve`: threaded static + live-RPC mounting via `--app`,
  `/healthz`, immutable asset caching, traversal containment.
- `pyweb deploy`: Dockerfile (healthcheck + serve CMD), compose, K8s
  with liveness/readiness probes. No mandatory cloud.
- Branded, overridable HTML error pages (XSS-escaped path + request id).

### DX & docs
- `pyweb dev` hot-reload, `pyweb test|fmt|lint`, `pyweb --version`,
  `pyweb npm` TS-declaration stubs, 10-page docs site
  (`website/build.py`, stdlib-only) + guides `00–14`.
- Every example app (`counter todo blog auth chat showcase`) compiles,
  builds, and passes `check` in the suite.

### Fixed
- SSR stable `data-pw-id` hydration markers; `runtime.js on()` handler
  refs; `bind_text` computed-thunk resolution; `cache(minutes=)` unit
  bug; form CSRF token rendering; bench now includes runtime bytes in
  shipped totals (previously under-reported).

## [0.1.0] — prototype
- Initial compiler (parser, reactivity, placement, RPC, codegen),
  runtimes, CLI, counter + todo demos, 141-test suite.
