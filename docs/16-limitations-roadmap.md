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
- **Dev reload is a full page reload.** State is not preserved across
  edits.

## Released so far

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
   after 0.5 has been used in production for a while.
2. **File uploads to object storage from the playground and the admin**,
   and garbage collection of unused uploads.
3. **State-preserving hot reload** while developing.

Ideas, bug reports and pull requests are welcome; see
[CONTRIBUTING.md](https://github.com/MaanavKrishna/PyWeb/blob/main/CONTRIBUTING.md).
