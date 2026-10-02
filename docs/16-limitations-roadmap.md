# Limitations and roadmap

PyWeb 0.1 is deliberately focused. These are the current limits, so you
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
- **Take-over, not hydration.** After the server renders a page, the
  page module rebuilds the DOM with live bindings rather than attaching
  to the existing nodes. Text typed before the module loads is reset.
- **Realtime is polling-based.** There is no persistent push channel to
  the browser yet; poll a server function (as the chat example does)
  or `/__pyweb/poll`.
- **No npm imports in browser code.** Browser code can use the
  browser's own APIs but not npm packages.
- **No language server.** Editors treat `.pyweb` files as Python with
  unusual lines; there is no completion or inline error reporting yet.
- **Dev reload is a full page reload.** State is not preserved across
  edits.

## Roadmap

Planned, roughly in order. Nothing here is promised for a date.

1. **Hydration**: attach to server-rendered nodes instead of rebuilding.
2. **Push updates**: a streaming channel (Server-Sent Events) for
   `realtime` and live queries, with the polling path as fallback.
3. **Multi-file apps**: components imported from other `.pyweb` files.
4. **Editor support**: a language server for `.pyweb` (diagnostics,
   completion, go-to-definition) built on the compiler.
5. **npm interop**: importing ES modules into browser code with typed
   stubs generated from TypeScript declarations.
6. **Client-side navigation** between pages of the same app.

Ideas, bug reports and pull requests are welcome; see
[CONTRIBUTING.md](https://github.com/MaanavKrishna/PyWeb/blob/main/CONTRIBUTING.md).
