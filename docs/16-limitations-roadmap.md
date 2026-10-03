# Limitations and roadmap

PyWeb is deliberately focused. These are the current limits, so you
can decide up front whether they matter for your app.

## Current limitations

- **Browser code is a Python subset.** See
  [Python in the browser](07-browser-python.md) for exactly what
  compiles. Anything else must live in an `@server` function.
- **Multi-page navigation.** Links do full page loads (fast, because
  pages are small and the runtime is cached). There is no client-side
  router.
- **npm packages must ship ES modules.** CommonJS-only packages and
  packages built on Node.js modules can't run in the browser; see
  [npm packages](18-npm-packages.md).
- **Dev reload is a full page reload.** State is not preserved across
  edits.

## Released so far

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

1. **Client-side navigation** between pages of the same app.

Ideas, bug reports and pull requests are welcome; see
[CONTRIBUTING.md](https://github.com/MaanavKrishna/PyWeb/blob/main/CONTRIBUTING.md).
