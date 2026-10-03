# Limitations and roadmap

PyWeb is deliberately focused. These are the current limits, so you
can decide up front whether they matter for your app.

## Current limitations

- **Browser code is a Python subset.** See
  [Python in the browser](07-browser-python.md) for exactly what
  compiles. Anything else must live in an `@server` function.
- **One file per app for pages and components.** Server code can import
  any Python module next to `app.pyweb`; pages and components must be
  defined in the `.pyweb` file itself.
- **Multi-page navigation.** Links do full page loads (fast, because
  pages are small and the runtime is cached). There is no client-side
  router.
- **No npm imports in browser code.** Browser code can use the
  browser's own APIs but not npm packages.
- **No language server.** Editors treat `.pyweb` files as Python with
  unusual lines; there is no completion or inline error reporting yet.
- **Dev reload is a full page reload.** State is not preserved across
  edits.

## Released so far

- **0.2**: tools for building with AI assistants: the `pyweb mcp` server,
  an AI guide, project templates with `AGENTS.md`/`CLAUDE.md`, and
  `llms.txt`.
- **0.1**: the compiler, reactive runtime, server rendering, typed RPC,
  sessions, database layer and CLI.

See the [changelog](https://github.com/MaanavKrishna/PyWeb/blob/main/CHANGELOG.md) for details.

## Roadmap

Planned, roughly in order. Nothing here is promised for a date.

3. **Multi-file apps**: components imported from other `.pyweb` files.
4. **Editor support**: a language server for `.pyweb` (diagnostics,
   completion, go-to-definition) built on the compiler.
5. **npm interop**: importing ES modules into browser code with typed
   stubs generated from TypeScript declarations.
6. **Client-side navigation** between pages of the same app.

Ideas, bug reports and pull requests are welcome; see
[CONTRIBUTING.md](https://github.com/MaanavKrishna/PyWeb/blob/main/CONTRIBUTING.md).
