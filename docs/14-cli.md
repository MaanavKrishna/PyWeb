# Command line

```text
pyweb new NAME [--template T]        create NAME/ with app.pyweb, test_app.py, AGENTS.md, CLAUDE.md, static/;
                                     T = blank | counter (default) | todo | blog | saas | auth | chat | ai-chat
                                     (apps with a database also get migrations/0001_initial.py and a README)
pyweb dev FILE [--port 8000] [--host 127.0.0.1] [--no-reload]
                                     development server: recompile on save, live reload, error overlay,
                                     the dev toolbar, and background jobs in the same process
pyweb check FILE [--production]      compile + security checks; --production also checks the environment
pyweb inspect FILE [--security]      where every name runs and why; RPC table; findings
pyweb build FILE [--out dist] [--production] [--budget PATH=SIZE ...]
                                     self-contained dist/; --production hashes and minifies
pyweb serve [DIR] [--host 0.0.0.0] [--port $PORT|8000] [--workers N] [--migrate]
                                     production server for a built dist/ (N processes)

pyweb db upgrade [--contract] [--target M]    apply migrations (under a lock)
pyweb db downgrade [--steps N] [--to M]       revert the last migrations
pyweb db status                               applied, pending, and drift from the Models
pyweb db diff [--name N] [--rename T.OLD=NEW] [--allow-destructive]
                                              write the next migration from the Models
pyweb db check [--json]                       exit 1 if the Models changed without a migration (CI)
pyweb db seed [--seeds seeds.py]              run seeds.py (the app's Models are in scope)
pyweb db new|adopt|squash                     an empty migration / adopt an existing database / squash
        common: [--app app.pyweb] [--database URL] [--migrations DIR]

pyweb worker [APP] [--queues a,b] [--concurrency 8] [--no-schedule] [--metrics-port P]
                                     run background jobs and schedules until SIGTERM
pyweb worker --alive [--max-age 90]  health check for a worker container (exit 0 or 1)
pyweb jobs list|retry ID...|purge|run [--app APP] [--state S] [--name N]
                                     inspect, retry, clean up or run queued jobs

pyweb deploy TARGET [--replicas N] [--processes N] [--db sqlite|postgres|mysql] [--with redis]
             [--domain D] [--region R] [--name N] [--db-url URL] [--plan] [--check] [--out DIR]
                                     TARGET = docker | compose | k8s | fly | render | railway:
                                     plan the topology, explain it, write the files

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
budget breaches, `db check` exits `1` when a migration is missing, and
`deploy --check` exits `1` when the plan has warnings, so they can gate CI:

```yaml
- run: pyweb check app.pyweb --production
- run: pyweb db check --app app.pyweb
- run: pytest
```

## Editor support

`pyweb lsp` is a Language Server Protocol server for `.pyweb` files. It
reports compile errors and security warnings as you type (including
errors in imported `.pyweb` files), shows on hover whether a name runs
in the browser or on the server and why (and, for npm packages, the
installed version and TypeScript signature), completes components,
props, HTML tags and installed npm packages, jumps to definitions across files, and outlines pages,
components and server functions.

It also knows your data, without running anything:

- after `Post.` it offers the Model's columns, relations and query methods;
  inside `where(`, `create(` and `get_or_create(` the field names (and
  lookups like `title__icontains=`); inside `include("` the relations;
  inside `order("` the columns; inside `Field(` its options;
- inside `name="` of a field in `<Form action={save}>`, the fields `save`
  accepts (never `readonly` or `private` ones), and an error for a name it
  doesn't have;
- a warning on the exact line where a loop reads a relation its query
  didn't `include()` (one query per row);
- when you save, "the Models changed without a migration", with a code
  lens above the first Model that writes it (`pyweb db diff`).

### VS Code

Install the **PyWeb** extension. It's in the VS Code Marketplace once
published; every [GitHub release](https://github.com/MaanavKrishna/PyWeb/releases)
also has a `.vsix` file (Extensions view → `...` → *Install from VSIX...*).
It adds highlighting and snippets (`model`, `fk`, `formpage`, `livepage`,
`job`, `cron`, `policy`, `useauth`, ...), the commands *Create Migration
from Model Changes* and *Apply Migrations*, and starts `pyweb lsp` with the Python
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

