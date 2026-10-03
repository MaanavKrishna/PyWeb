# Changelog

All notable changes to this project are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [semantic versioning](https://semver.org).

## [Unreleased]

### Added
- **npm packages without Node.js.** `pyweb add chart.js/auto` downloads a
  package from the npm registry, checks its sha512 checksum, follows its
  imports from the browser entry point and copies only the files it
  needs (and its dependencies) into `static/vendor/`, pinning versions in
  `pyweb.lock`. `pyweb add` with no arguments reinstalls from the lock;
  `pyweb remove` takes a package out. Browser code binds exports with
  `Chart = npm("chart.js/auto")` or `npm("pkg", "Export")`. Calling a
  class constructs it and keyword arguments become an options object.
  Each page imports only the packages it uses, through an import map
  that the Content Security Policy allows by hash. CommonJS-only and
  Node.js-only packages are refused with an explanation, and an
  uninstalled package is a compile error that names the command to run.
- **`ref={el}`** sets a page variable to an element, in time for
  `on_mount`, for libraries that draw into the page.
- Changing an attribute of a package's object (`chart.value = 5`)
  re-renders whatever shows it.
- Web components (custom elements from npm) work in markup.
- `pyweb lsp`: hover shows an npm binding's installed version and
  TypeScript signature; completion offers installed packages in
  `npm("` and a module's exports after `name.`.
- New docs page: npm packages.
- **Layouts.** `@app.layout` wraps every page (or, with
  `@app.layout("/admin")`, pages under a prefix) in shared markup;
  `{children}` marks where the page goes. Layouts nest, have their own
  server code, state and handlers, and pages can opt out with
  `layout=None` or pick one with `layout="Name"`.
- **Client-side navigation.** Links between pages fetch the next page's
  HTML and swap it in below the layouts both pages share, so layout
  state (an open menu, a player) survives. Links are prefetched on
  hover or focus, back/forward restore the scroll position, and anything
  unusual falls back to a full page load. `navigate("/path")` from
  `pyweb.browser` does the same from browser code; `on_unmount` runs
  when a page is left; `App(client_nav=False)` turns it off.
- **Current links.** Links to the current page get
  `aria-current="page"` and links to its parent sections
  `aria-current="true"`, on the server and after each navigation.
- **Head tags.** `@app.page(description=..., image=..., noindex=...)` and
  `head(title=..., description=...)` in page code add description, Open
  Graph and Twitter tags; `App(base_url=...)` adds canonical URLs and
  absolute image links.
- **Error pages in `.pyweb`.** `@app.error(404)` / `@app.error(500)` pages
  take `path`, `status`, `message` or `request_id` and use root layouts.
- **Typed query parameters.** Page parameters that aren't in the route
  are read from the query string and converted by annotation (`int`,
  `float`, `bool`, `list[...]`); bad or missing values answer 400.
- New docs page: Layouts & navigation.
- **Streaming server functions.** A `@server` function that `yield`s
  sends each value as it's produced (NDJSON over the same RPC endpoint);
  browser code reads it with `async for`. `stream.cancel()` or leaving the
  loop aborts the request and closes the generator on the server, so an
  upstream AI call stops too. Errors raised part-way arrive as
  `RPCError`. Works with `async def` generators, under `pyweb serve`,
  `pyweb dev` and ASGI, and `TestClient.rpc` returns the streamed values.
- **`<Markdown text={...} />`** (from `pyweb`) renders Markdown to safe
  HTML on the server and updates it in the browser as the text changes,
  with the same rules in both places. Raw HTML is shown as text and
  links only accept http(s), mailto and relative URLs.
- **`ai-chat` template and example**: a streaming chat with Stop and
  Markdown replies that talks to Anthropic, any OpenAI-compatible server
  (OpenAI, Ollama, vLLM, ...) or a built-in demo model, chosen by
  environment variables. `pyweb new NAME --template ai-chat`.
- New docs page: Building AI apps.
- **Live data.** `rows = live(db, "select ...", params)` in a page or
  layout renders the rows and keeps them current: writes through
  `pyweb.db` (and `pyweb.models`) announce their table after commit, each
  distinct query re-runs once per change (bursts coalesced) and sends
  rows to every page showing it only when they changed. Pages follow a
  signed feed; `db.notify("table")` covers writes made elsewhere. With
  `RedisBus` it works across processes, and any process can take over a
  query from the page's signed description.
- New docs page: Live data.

### Changed
- The experimental `pyweb.live` module (in-memory live tables) is now
  `pyweb.livetable`; `pyweb.live` is the new live-query function.
- `pyweb.npm` (TypeScript declarations to dataclasses) is now
  `pyweb.dts`, and its command is `pyweb dts`. `pyweb build` no longer
  writes an esm.sh import map; packages come from `pyweb.lock`.
- The default Content Security Policy adds `worker-src 'self' blob:` so
  packages can start Web Workers.
- The starter stylesheet styles Markdown and chat bubbles.
- The browser runtime is about 14 KB gzipped (was about 11 KB), for
  client-side navigation and npm support.
- New website design: a colour system where blue means the browser and
  amber the server, self-hosted Inter / Bricolage Grotesque / JetBrains
  Mono, dark code windows, and a landing page that shows each line of an
  app with where it runs (worked out by the compiler) next to the real
  compiled output.
- The example apps and the `pyweb new` stylesheet use the new palette.

## [0.3.0]

Faster pages that feel like an app, apps that span several files, and
tools for editors, the browser and AI assistants.

### Added
- **Hydration.** Pages now adopt the server-rendered DOM instead of
  rebuilding it: nodes are kept, focus and text typed before the script
  loads survive, and typed values reach their variables. When the server
  HTML doesn't match, the page falls back to a client render and logs
  a warning. The page root carries `data-pw-mode="hydrated"` or
  `"rendered"`.
- **Live updates.** `publish(name, data)` in server code pushes to every
  page that called `subscribe(channel_feed, handler)`, over Server-Sent
  Events with a polling fallback. Feeds come from `channel(name)` while
  rendering: signed, expiring, and positioned so no message published
  after the render is missed. `pyweb dev`, `pyweb serve` and the ASGI
  adapter stream; `realtime.use_bus()` shares the bus (e.g. Redis)
  between processes. The chat example uses it instead of polling.
- **Multi-file apps.** `from widgets import Card, save, COLOR` imports
  components, `@server` functions and constants from `widgets.pyweb` (or
  `ui/cards.pyweb` as `ui.cards`). Each file keeps its own names in the
  generated JavaScript, imported files run as real modules on the
  server, `pyweb build` copies them into `dist/`, and errors name the
  file they're in. Pages stay in the app file; circular imports and
  duplicate server-function names are compile errors.
- **Editor support.** `pyweb lsp` is a language server (stdio, standard
  library only): compile errors and security warnings as you type,
  hover showing where each name runs and why, completion for components,
  props and tags, go to definition across files, and an outline. The new
  VS Code extension (`editors/vscode`) adds highlighting and snippets and
  starts it; releases attach a `.vsix`. Setup for Neovim and Helix is in
  the command-line docs.
- **Playground** on the website: edit an app and run it in the browser,
  with the real compiler, server rendering, `@server` functions,
  sessions and live updates running on Pyodide. Shows the compiled
  JavaScript and what runs where, reports errors with a fix, and shares
  code as links.
- **MCP tools** `pyweb_screenshot` (open a page in headless Chromium,
  run click/fill/press steps, get a screenshot, the page text, console
  errors and hydration status) and `pyweb_test` (run the app's pytest
  tests and report failures). New apps include a starter `test_app.py`.
- `python -m pyweb.bench --max-ssr-ms N --max-compile-ms N` fails when
  a benchmark exceeds its budget; CI runs it.

### Removed
- The old `pyweb.lsp` helper functions (`complete`, `DocumentState`,
  `complete_*`, `diagnostics_for_compile_error`, `hover(compiled, name)`,
  `boundary_lens`); they described an earlier API and no editor used them.

### Changed
- `import pyweb` no longer needs the `sqlite3` module (it's imported when
  a SQLite database is opened), and password hashing falls back to a
  pure-Python PBKDF2 on Pythons without OpenSSL. Both make PyWeb run on
  Pyodide.
- `/__pyweb/events` and `/__pyweb/poll` require a signed `?feed=`; the
  unauthenticated `?channel=` form is gone, so channels are no longer
  readable by anyone who guesses a name.
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
