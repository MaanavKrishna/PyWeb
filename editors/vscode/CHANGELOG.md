# Changelog

## 0.5.0

- Completion knows your data: Model fields and query methods after `Post.`,
  field names in `where(`/`create(`, relations in `include("`, columns in
  `order("`, `Field(` options, and a `<Form>`'s fields in `name="`.
- New warnings: a form field the action doesn't have, a loop that reads a
  relation its query didn't `include()` (N+1), and Models changed without a
  migration, with a code lens that writes it.
- New commands: Create Migration from Model Changes, Apply Migrations.
- New snippets: `model`, `fk`, `formpage`, `livepage`, `job`, `cron`,
  `policy`, `useauth`.
- Updated to the current language client (vscode-languageclient 10, LSP
  3.18). The extension now needs VS Code 1.91 or later.
- Bundled with esbuild: the package is 12 files instead of 364, and the
  extension loads faster.
- Tested in a real VS Code (the oldest supported version and the current
  stable one) against the real language server on every change.
- Needs PyWeb 0.5 for the data features.

## 0.4.4

- Released with PyWeb 0.4.4 (security and production hardening; see the
  main changelog). No changes to the extension itself.

## 0.4.3

- Released with PyWeb 0.4.3 (fixes, security and speed; see the main
  changelog). No changes to the extension itself.

## 0.4.2

- Fixed "spawn python ENOENT" when no Python interpreter is selected: the
  extension finds a Python that has PyWeb, offers to install it when it's
  missing, and restarts the server when you change interpreter.
- New commands: Run App (run button and Ctrl+F5), New App, Check App, Add
  npm Package, Install or Update PyWeb, Set Up AI Assistant (MCP).
- Closing tags are added as you type (`pyweb.autoCloseTags`), and Emmet
  works in markup.
- New snippets: `layout`, `error`, `stream`, `livequery`, `npm`, `mount`.
- A status item shows the PyWeb version the language server uses.
- An icon.

## 0.4.1

- Released with PyWeb 0.4.1. The marketplace page lists the npm, layout
  and error-page support added in 0.4.0.

## 0.4.0

- Hover on a name bound with `npm(...)` shows the installed version and
  its TypeScript signature; completion offers installed packages inside
  `npm("` and a module's exports after `name.`.
- The outline shows layouts and error pages.
- Needs PyWeb 0.4 for these (older versions still work without them).

## 0.3.0

First release: highlighting, snippets, and the PyWeb language server
(diagnostics, hover, completion, go to definition, outline).
