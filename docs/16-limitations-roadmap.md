# Limitations and roadmap

PyWeb is deliberately focused. These are the current limits, so you
can decide up front whether they matter for your app.

## Current limitations

- **Browser code is a Python subset.** See
  [Python in the browser](07-browser-python.md) for exactly what
  compiles. Anything else must live in an `@server` function.
- **npm packages must ship ES modules.** CommonJS-only packages and
  packages built on Node.js modules can't run in the browser; see
  [npm packages](18-npm-packages.md).
- **Time travel moves the page's state only.** The dev timeline puts the
  page's and components' variables back; data on the server isn't
  rolled back.
- **SQLite is for one machine.** Several processes on one host share it
  fine, but more than one server needs Postgres or MySQL;
  `pyweb check --production` and `pyweb deploy` say so.
- **Desktop apps are a window around your app.** They run the same
  server on the user's machine; there's no mobile app target, and
  installers aren't signed for you (see [Desktop apps](32-desktop.md)).
- **Tenant isolation is enforced by the database only on Postgres.** On
  SQLite and MySQL it comes from PyWeb's query scoping, so raw SQL there
  must filter by organization itself (see [Teams](30-teams.md)).
- **The playground can't upload files**, and runs background jobs right
  after each request (the browser's Python has no threads).

## Released so far

- **0.6**: teams and beyond the web. Organizations with roles,
  invitations and tenant-scoped Models (enforced by Postgres row-level
  security), API tokens and OpenAPI, webhooks in and out, installable apps
  with Web Push, desktop windows and installers, a UI kit with themes and
  motion, Trusted Types, encrypted fields and a threat model, about 2.5×
  faster serving, Python 3.15 and free-threaded builds, and a much richer
  MCP server. 0.6.1 added error messages with code frames and
  suggestions, `pyweb doctor`, dev reloads that keep page state, and 4×
  faster reloads and tests. 0.6.2 made the [dev loop](35-dev-loop.md):
  saves update the page in place with component state kept, Alt-click
  opens any element's source, Fix with AI and Edit with AI, a
  time-travel timeline, `pyweb dev --share`, a faster first start and a
  guided `pyweb new`. 0.6.3 was a security and reliability release plus
  what apps missed most: HTTP routes and middleware, page caching with
  ETags, translations, `<ErrorBoundary>`, test helpers, source maps and
  `pyweb dev --debug`, CSV export, drag-and-drop uploads, a `Stepper`,
  responsive `<Image>`, and rename and find-references in the editor.
- **0.5**: production apps. Models with relations and migrations, forms
  checked in the browser and on the server, an auth kit (passwords,
  passkeys, emailed links, OAuth, two-step sign-in, an admin), row-level
  live updates over WebSockets on PyWeb's own server, durable background
  jobs and schedules, `pyweb deploy` for six targets, logs, metrics,
  tracing and a dev toolbar, data-aware editor and MCP tools, the `saas`
  template and the second playground.
- **0.4**: npm packages without Node.js, layouts and client-side
  navigation, page head tags and `.pyweb` error pages, typed query
  parameters, streaming server functions and `<Markdown>` for AI apps,
  live queries, and the dashboard, site and AI chat examples.
- **0.3**: hydration, live updates over Server-Sent Events, multi-file
  apps, the `pyweb lsp` language server and VS Code extension, the
  browser playground, faster server rendering, and screenshot and test
  tools for AI assistants.
- **0.2**: tools for building with AI assistants: the `pyweb mcp` server,
  an AI guide, project templates with `AGENTS.md`/`CLAUDE.md`, and
  `llms.txt`.
- **0.1**: the compiler, reactive runtime, server rendering, typed RPC,
  sessions, database layer and CLI.

See the [changelog](https://github.com/MaanavKrishna/PyWeb/blob/main/CHANGELOG.md) for details.

## Roadmap

Planned, roughly in order. Nothing here is promised for a date.

1. **1.0**: the API, RPC protocol and CLI frozen under semantic versioning,
   after 0.6 has been used in production for a while.
2. **0.7: the next things apps ask for.**
   - Billing: Stripe Checkout and the customer portal, plans tied to the
     organization's features, webhooks handled for you.
   - An in-app notifications center (a bell, unread counts, live), with
     email and push from the same call.
   - Full-text search on Models (SQLite FTS5, Postgres `tsvector`, MySQL
     `FULLTEXT`) behind one `Model.search()`.
   - A rich-text editor component whose output is sanitized on the server.
   - Feature flags and A/B tests per user or organization.
   - Streaming server rendering: `<Await>` sends the page shell first and
     slow parts as they finish.
   - Pages built ahead of time from data, rebuilt on a schedule or on a
     write (incremental static regeneration).
   - `pyweb typecheck`: pyright over the Python in `.pyweb` files.
   - Admin customization per Model: list columns, filters, actions, forms.
3. **File uploads from the playground.**
4. **Fix with AI in the editor**, from the same proposals the dev page
   shows (diff, apply, undo), through the VS Code extension.

Ideas, bug reports and pull requests are welcome; see
[CONTRIBUTING.md](https://github.com/MaanavKrishna/PyWeb/blob/main/CONTRIBUTING.md).
