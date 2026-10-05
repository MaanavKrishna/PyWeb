# Command line

```text
pyweb new NAME [--template T]        create NAME/ with app.pyweb, test_app.py, AGENTS.md, CLAUDE.md, static/;
                                     T = blank | counter (default) | todo | blog | auth | chat | ai-chat
pyweb dev FILE [--port 8000] [--host 127.0.0.1] [--no-reload]
                                     development server: recompile on save, live reload,
                                     in-browser error overlay with the failing line
pyweb check FILE                     compile + security checks; non-zero exit on problems
pyweb check FILE --production        also check the environment is ready for production
pyweb inspect FILE [--security]      where every name runs and why; RPC table; findings
pyweb build FILE [--out dist] [--production] [--budget PATH=SIZE ...]
                                     self-contained dist/; --production hashes and minifies;
                                     budgets fail the build when a file exceeds SIZE (e.g. 20KB)
pyweb serve [DIR] [--host 0.0.0.0] [--port 8000] [--workers N] [--migrate]
                                     production server for a built dist/ (N processes)
pyweb db migrate|status|rollback|new [--database URL] [--migrations DIR]
                                     [--name NAME] [--steps N] [--to VERSION]
pyweb deploy [--target docker|compose|k8s] [--out deploy] [--port 8000]
                                     write deployment files
pyweb add [PKG[@RANGE] ...] [--app DIR]
                                     download npm packages for browser code into static/vendor/
                                     and pin them in pyweb.lock; no arguments: reinstall from the lock
pyweb remove PKG ... [--app DIR]     remove npm packages (see npm packages)
pyweb dts FILE.d.ts                  Python dataclasses from TypeScript declarations
pyweb mcp                            MCP server over stdio for AI assistants (see AI assistants & MCP)
pyweb lsp                            language server over stdio for editors (see below)
pyweb test [PATH]                    run pytest
pyweb fmt [PATH] / pyweb lint [PATH] run ruff format / ruff check (if installed)
pyweb --version
```

`python -m pyweb.cli ...` is equivalent to `pyweb ...`.

## Exit codes

`check` and `build` exit `1` on compile or security errors and `2` on
budget breaches, so they can gate CI.

## Editor support

`pyweb lsp` is a Language Server Protocol server for `.pyweb` files. It
reports compile errors and security warnings as you type (including
errors in imported `.pyweb` files), shows on hover whether a name runs
in the browser or on the server and why (and, for npm packages, the
installed version and TypeScript signature), completes components,
props, HTML tags and installed npm packages, jumps to definitions across files, and outlines pages,
components and server functions.

### VS Code

Install the **PyWeb** extension. It's in the VS Code Marketplace once
published; every [GitHub release](https://github.com/MaanavKrishna/PyWeb/releases)
also has a `.vsix` file (Extensions view → `...` → *Install from VSIX...*).
It adds highlighting and snippets, and starts `pyweb lsp` with the Python
interpreter selected in the Python extension. PyWeb 0.3 or later must be
installed in that environment; set `pyweb.server.command` to use a
different command.

### Other editors

Any LSP client works. Use `pyweb lsp` as the command and `.pyweb` as the
file type. Neovim (0.11+):

```lua
vim.filetype.add({ extension = { pyweb = "pyweb" } })
vim.lsp.config("pyweb", { cmd = { "pyweb", "lsp" }, filetypes = { "pyweb" }, root_markers = { "app.pyweb" } })
vim.lsp.enable("pyweb")
```

Helix (`languages.toml`):

```toml
[language-server.pyweb]
command = "pyweb"
args = ["lsp"]

[[language]]
name = "pyweb"
scope = "source.pyweb"
file-types = ["pyweb"]
language-servers = ["pyweb"]
grammar = "python"
```

