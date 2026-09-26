# BUGLOG (Track E - QA)

Framework bugs found while building the QA harness + reference apps.
Per track rules, Track E does **not** fix framework code; this log is the
handoff to the integration/framework tracks.

Format per entry: repro + expected behavior.

## Resolution status (fix-all pass, 2026-09-25)

- **QA-001 — RESOLVED (already fixed on main).** `import pyweb` works;
  `pyweb` CLI exposes build/dev/new/check/npm/deploy/test/fmt/lint.
- **QA-002 — RESOLVED (already fixed on main).** `.pyweb` examples exist
  under `examples/*/`; compiler maps source -> SSR HTML + RPC + hydration
  markers (`pw-bind`, `data-pw-id`), covered by `test_compiler.py`,
  `test_codegen.py`, `test_docs_snippets.py`.
- **QA-003 — RESOLVED (already fixed on main).** `pyweb dev` boots an
  in-process server (`Server.handle` + `cmd_dev` ephemeral-port serving).
- **QA-004 — FIXED in this pass.** Compiler now emits versioned script
  refs (`/static/<page>.js?v=<sha8>`); dev handler strips the query and
  sends `Cache-Control: immutable` for versioned URLs.
  Tests: `tests/test_static_hashing.py` (2 tests).
- **QA-005 — FIXED in this pass.** `GET /__pyweb/events?channel=` streams
  SSE (`text/event-stream`, `id:`/`event:`/`data:`, `last_id` resume);
  `GET /__pyweb/poll?channel=&since=` is the JSON fallback; browser
  `subscribe()` uses SSE with poll fallback.
  Tests: `tests/test_realtime_transport.py` (8 tests).
- **QA-006 — FIXED in this pass.** `pyweb.auth` now pins the contract:
  unauthenticated -> `302 Location: /login?next=<path>`; credentials via
  `pyweb_session` cookie or `Authorization: Bearer`; `login_response` /
  `logout_response` manage the `HttpOnly` cookie.
  Tests: `tests/test_auth_contract.py` (7 tests).
- **Test-hygiene — FIXED in this pass.** All `ResourceWarning`s eliminated:
  bare `open().read()` -> `Path.read_text()`; test HTTP servers call
  `server_close()`; `SQLiteDB` gained a `__del__` pool-drain safety net.
  Suite passes with `-W error::ResourceWarning` (255 passed).

## QA-001: `pyweb` framework package absent from main

- Repro: `python3 -c "import pyweb"` on `origin/main` (`f8efd35`) ->
  `ModuleNotFoundError: No module named 'pyweb'`.
- Also: `which pyweb` finds no `pyweb build` CLI.
- Expected: an importable `pyweb` package (or documented install step)
  exposing at least one of `pyweb.compiler.{compile_file, compile,
  build, render}` or a `pyweb build` CLI so `compile_pyweb()` can do
  real work.
- QA impact: all framework-dependent paths skip via
  `FrameworkNotAvailable` (see `tests/e2e_harness.py::require_framework`).
  Harness self-tests run green against the stdlib stub server instead.

## QA-002: no `.pyweb` file-format spec on main

- Repro: searched repo for `*.pyweb`, `pyweb/*`, compiler docs -> none
  exist on `origin/main` (repo is README-only).
- Expected: a spec (or reference implementation) defining how `.pyweb`
  source maps to SSR HTML, RPC names, and hydration markers
  (`data-pw-id`, `pw-bind`) so example apps can be validated as more
  than static HTML.
- QA impact: reference apps under `examples/*/` are written in the
  documented-by-harness convention; `test_build_clean`
  skips-with-pass until a real compiler lands, at which point the same
  tests assert for real with no test changes needed.

## QA-003: no server entry point discoverable

- Repro: no `pyweb.{serve,server,cli,app}` module and no `pyweb serve`
  CLI on main.
- Expected: one documented way to boot an app's server in-process on an
  ephemeral port (host/port params) for HTTP assertion.
- QA impact: `boot_pyweb_app()` probes the likely entry points and
  raises `FrameworkNotAvailable` otherwise; per-app serve tests use the
  stub server contract in the meantime.

## QA-004: static-asset hashing unimplemented

- Repro: no build pipeline exists to emit `app.<hash>.js`-style
  references.
- Expected: hashed asset URLs in SSR HTML with immutable cache headers,
  resolvable via HTTP 200.
- QA impact: `assert_static_hashing()` skips gracefully when no hashed
  refs are present; `check_static_hashing()` asserts resolvability when
  they are. No test changes needed once implemented.

## QA-005: realtime transport (SSE) unimplemented

- Repro: no `/__pyweb/events` endpoint exists in any shippable server.
- Expected: either SSE at `/__pyweb/events` (`text/event-stream`) or an
  explicit, documented polling fallback at `/__pyweb/poll`.
- QA impact: chat reference app declares `sse-with-poll-fallback`;
  harness self-tests assert both shapes against the stub. Filed as a
  framework gap, not a QA failure.

## QA-006: auth session/redirect behavior unspecified

- Repro: no session, cookie, or redirect semantics documented on main.
- Expected: specified contract for protected routes - at minimum the
  status code (`302`) and `Location` (`/login`) for unauthenticated
  access, plus how an authenticated request presents credentials.
- QA impact: auth reference app + `TestAuthApp` pin the stub contract
  (`302 Location: /login` unauthenticated, `200` with `Authorization`
  header). Framework track should confirm or amend.
