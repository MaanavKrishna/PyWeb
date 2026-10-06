# PyWeb for Visual Studio Code

Language support for [PyWeb](https://maanavkrishna.github.io/PyWeb/) `.pyweb`
files: full-stack web apps written in Python.

## Features

- **Run your app** with the ▶ button on a `.pyweb` file (or Ctrl+F5).
  `pyweb dev` starts in a terminal and reloads as you edit.
- **New App** from a starter: a small SaaS (accounts, data, live
  updates, jobs, an admin), counter, to-do, blog, auth, chat or a
  streaming AI chat.
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
- **Completion that knows your data**: a Model's fields and query methods
  after `Post.`, field names in `where(`/`create(` (with lookups like
  `title__icontains=`), relations in `include("`, columns in `order("`,
  `Field(` options, and a `<Form>`'s fields in `name="`.
- **Data warnings**: a form field the action doesn't take, a loop that
  reads a relation its query didn't `include()` (an N+1 query), and Models
  that changed without a migration, with a **code lens** that writes the
  migration in one click.
- **Closing tags** are added when you type the `>` of an opening tag, and
  **Emmet** abbreviations work in markup.
- **Go to definition** for components, server functions and helpers,
  across files.
- **Outline** of pages, layouts, error pages, components and server
  functions.
- **Snippets**: `pyweb` (new app), `page`, `layout`, `component`,
  `server`, `stream`, `handler`, `mount`, `for`, `live`, `livequery`,
  `npm`, `error`, and for data: `model`, `fk`, `formpage`, `livepage`,
  `job`, `cron`, `policy`, `useauth`.
- **AI assistants**: **Set Up AI Assistant (MCP)** adds the PyWeb MCP
  server to `.vscode/mcp.json`, so Copilot's agent mode can check,
  render, test and build your app.

## Commands

Open the Command Palette (Ctrl+Shift+P) and type "PyWeb":

| Command | What it does |
|---|---|
| PyWeb: Run App (dev server) | `pyweb dev` for the open file, or the workspace's `app.pyweb` |
| PyWeb: New App... | Pick a template and a name, then open the new app |
| PyWeb: Check App | `pyweb check`: compile and run the security checks |
| PyWeb: Create Migration from Model Changes | `pyweb db diff`: write a migration for what changed in your Models |
| PyWeb: Apply Migrations (dev database) | `pyweb db upgrade` for the app's database |
| PyWeb: Add npm Package... | `pyweb add`: an npm package for browser code, no Node.js needed |
| PyWeb: Install or Update PyWeb | `pip install -U pyweb-stack` with the Python VS Code found |
| PyWeb: Set Up AI Assistant (MCP) | Add `pyweb mcp` to `.vscode/mcp.json` |
| PyWeb: Restart Language Server | After changing environments |

The status item on `.pyweb` files (bottom right, next to "PyWeb") shows
the PyWeb version in use, or what's wrong and how to fix it.

## Requirements

VS Code 1.91 or later. Highlighting, snippets and tag closing work on
their own. Everything else comes from PyWeb 0.3 or later (0.4 or later
for npm packages, layouts and error pages, 0.5 or later for Models,
forms and migrations):

```bash
pip install -U pyweb-stack
```

The extension uses the first of these that has PyWeb: the interpreter
selected in the Python extension, `pyweb` on your `PATH`, `py -3` (on
Windows), `python3`, `python`. If PyWeb isn't installed, it offers to
install it. It restarts by itself when you pick another interpreter.

## Settings

| Setting | Default | |
|---|---|---|
| `pyweb.server.command` | `[]` | The command that starts the language server, e.g. `["/path/to/venv/bin/pyweb", "lsp"]`. When it ends in `lsp`, the rest runs the other commands too. |
| `pyweb.dev.port` | `8000` | Port for Run App |
| `pyweb.dev.openBrowser` | `false` | Open the browser after Run App starts |
| `pyweb.autoCloseTags` | `true` | Add closing tags as you type |

## Troubleshooting

**"spawn python ENOENT"** (extension 0.4.1 and older): no interpreter was
selected, so `python` was used and doesn't exist. Update the extension,
or run **Python: Select Interpreter**, or set
`"pyweb.server.command": ["python3", "-m", "pyweb.cli", "lsp"]`.

**"PyWeb isn't installed for ..."**: click **Install PyWeb**, or run the
`pip install` command it shows, then **PyWeb: Restart Language Server**.

The **Output** panel ("PyWeb" channel) shows the language server's log.

## Other editors

`pyweb lsp` is a standard Language Server Protocol server over stdio, so
Neovim, Helix, Zed, Sublime Text and others can use it too. See the
[editor setup docs](https://maanavkrishna.github.io/PyWeb/cli.html).
