# PyWeb for Visual Studio Code

Language support for [PyWeb](https://maanavkrishna.github.io/PyWeb/) `.pyweb`
files: full-stack web apps written in Python.

## Features

- **Highlighting** for Python and markup together: tags, components,
  attributes, event handlers and `{expressions}` (highlighted as Python).
- **Errors as you type** from the PyWeb compiler, on the right line,
  including errors in other `.pyweb` files you import. Security checks
  show as warnings.
- **Hover** over a name to see whether it runs in the browser or on the
  server, and why. Hover a component for its props, a server function
  for its signature and RPC endpoint, or a name bound with `npm(...)` for
  the installed version and its TypeScript signature.
- **Completion** for components and HTML tags after `<`, a component's
  props inside its tag, names after `from pyweb import`, installed npm
  packages inside `npm("`, and a package's exports after `name.`.
- **Go to definition** for components, server functions and helpers,
  across files.
- **Outline** of pages, layouts, error pages, components and server
  functions.
- **Snippets**: `pyweb` (new app), `page`, `component`, `server`,
  `handler`, `for`, `live`.

## Requirements

Highlighting and snippets work on their own. Everything else comes from
the language server that ships with PyWeb 0.3 or later (0.4 or later for
npm packages, layouts and error pages):

```bash
pip install -U pyweb-stack
```

The extension starts `python -m pyweb.cli lsp` with the interpreter
selected in the Python extension, or `pyweb lsp` from your `PATH`. To use
something else, set `pyweb.server.command`, for example:

```json
"pyweb.server.command": ["/path/to/venv/bin/pyweb", "lsp"]
```

Run **PyWeb: Restart Language Server** after changing environments.

## Other editors

`pyweb lsp` is a standard Language Server Protocol server over stdio, so
Neovim, Helix, Zed, Sublime Text and others can use it too. See the
[editor setup docs](https://maanavkrishna.github.io/PyWeb/cli.html).
