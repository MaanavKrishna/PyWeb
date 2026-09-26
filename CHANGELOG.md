# PyWeb Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- `examples/showcase/`: live demo app (counter, computed `total = price x quantity`,
  two-way search binding, typed RPC echo) with cache-busted dev server on port 12000.
- `.gitignore` covering `__pycache__/`, `*.pyc`, `dist/`, `.venv/`, `node_modules/`.

### Fixed
- Browser reactivity wiring: SSR now emits stable `data-pw-id` on interactive elements,
  matched by generated JS bindings (previously document-order tagging mis-attached listeners).
- `runtime.js` `on()` now accepts handler function references (previously treated them as
  scope string keys and silently attached nothing).
- `bind_text` resolves computed thunk values before rendering.

## [0.1.0] — prototype
- Initial compiler (parser, reactivity, placement, RPC, codegen), runtimes, CLI,
  counter + todo demos, 141-test suite.
