# Command line

```text
pyweb new [NAME] [--template T] [--theme PRESET]
                                     create NAME/ with app.pyweb, test_app.py, AGENTS.md, CLAUDE.md, static/;
                                     T = blank | counter (default) | todo | blog | saas | auth | chat | ai-chat | desktop
                                     (apps with a database also get migrations/0001_initial.py and a README);
                                     PRESET = indigo | zinc | slate | rose | ocean | violet | emerald | amber
                                     (written into App(theme=...)); with no NAME, in a terminal, it asks
pyweb dev FILE [--port 8000] [--host 127.0.0.1] [--no-reload] [--no-open] [--share] [--debug [--debug-port 5678] [--wait]]
                                     development server (see The dev loop): saves update the open page
                                     in place, keeping page and component state (?fresh starts over);
                                     Alt-click opens an element's line in your editor, Alt+Shift-click
                                     changes it with AI, error pages have Fix with AI; the dev toolbar
                                     (requests, SQL, jobs, emails, theme, motion, a time-travel timeline);
                                     background jobs in the same process; opens the browser (not in CI,
                                     over SSH or with --no-open); --host 0.0.0.0 prints the network URL
                                     with a QR code; --share prints a temporary public link; writes
                                     .pyweb/dev.json so `pyweb mcp` can see its errors and logs
pyweb check FILE [--production] [--strict]
                                     compile, security and accessibility checks; --production also checks
                                     the environment; --strict fails on warnings too
pyweb doctor [FILE] [--production] [--json] [--port 8000]
                                     is the project ready? Python, the app compiles, optional packages,
                                     database and migrations, Redis, secrets in the source, upgrade
                                     changes, a stale dev server link, tests, the port, the editor; a fix
                                     for each problem (exit 1 on any failure; --json for CI)
pyweb upgrade [FILE] [--fix]         what to change for this PyWeb version (file and line); --fix applies
                                     the safe rewrites
pyweb inspect FILE [--security]      where every name runs and why; RPC table; findings
pyweb build FILE [--out dist] [--production] [--sourcemaps] [--budget PATH=SIZE ...]
                                     self-contained dist/; --production hashes and minifies
pyweb serve [DIR] [--host 0.0.0.0] [--port $PORT|8000] [--workers N] [--migrate]
                                     production server for a built dist/ (N processes);
                                     needs PYWEB_AUTH_SECRET (or PYWEB_ENV=development)
pyweb ui list | add NAME... [--force] | diff NAME   [--dir .]
                                     the UI kit's components; copy one into components/ to change
                                     it; show how your copy differs from the kit
pyweb files clean [--dry-run] [--grace SECONDS] [--app app.pyweb]
                                     delete uploaded files no row refers to (also runs daily)
pyweb desktop [FILE] [--dev] [--smoke] [--gui qt|gtk|...]
                                     run the app in its own window (needs pyweb-stack[desktop])
pyweb build FILE --desktop [--package] [--bundle com.example] [--app-version 1.0.0]
                                     a Briefcase project in dist-desktop/ (with --package: the installer)
pyweb push key | public               make a PYWEB_VAPID_KEY for push notifications; print the public key
pyweb openapi FILE [--out openapi.json] [--server URL]
                                     an OpenAPI 3.1 document for the app's server functions
pyweb profile FILE [--seconds 15] [--out PATH] [--port 8000] [--mode cpu|wall|gil]
                                     serve the app while you use it and record where the time goes:
                                     a flame graph on Python 3.15 (Tachyon), a text report before
                                     (see Performance)

pyweb db upgrade [--contract] [--target M]    apply migrations (under a lock)
pyweb db downgrade [--steps N] [--to M]       revert the last migrations
pyweb db status                               applied, pending, and drift from the Models
pyweb db diff [--name N] [--rename T.OLD=NEW] [--allow-destructive]
                                              write the next migration from the Models
pyweb db check [--json]                       exit 1 if the Models changed without a migration (CI)
pyweb db seed [--seeds seeds.py]              run seeds.py (the app's Models are in scope)
pyweb db reencrypt                            rewrite encrypted fields with the current PYWEB_DATA_KEY
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
pyweb i18n extract | init LANG | check [--strict] [--app app.pyweb]
                                     translation catalogs in locales/ (see Translations)
pyweb mcp                            MCP server over stdio for AI assistants (see AI assistants & MCP)
pyweb mcp --http [--port 8765] [--token T]   the same over Streamable HTTP (always with a bearer token)
pyweb lsp                            language server over stdio for editors (see below)
pyweb test [PATH]                    run pytest on PATH (a test file, a folder or app.pyweb's folder;
                                     default: the current folder, where new apps keep test_app.py)
pyweb fmt [PATH] / pyweb lint [PATH] run ruff format / ruff check (if installed)
pyweb --version
```

`python -m pyweb.cli ...` is equivalent to `pyweb ...`.

## Error messages

A compile error names the file and line, shows the line with a caret under
the problem, suggests the name you probably meant, and links the docs page
that explains it:

```text
error: app.pyweb:17: <Greeting> has no prop 'nme'; did you mean 'name'?

  16 |         <h1>{count}</h1>
> 17 |         <Greeting nme="x" />
     |                   ^
  18 |     </main>

hint: Use one of the component's parameter names as the attribute.
docs: https://maanavkrishna.github.io/PyWeb/language.html
```

The first line keeps the `file:line: message` shape, so editors and CI
logs can jump to it. `pyweb check` also warns about kit props and motion
values that won't work (`<Button variant="outlin">`). The `pyweb dev`
overlay shows the same, and when page code raises, its error page starts
with the frames in your own files and their source lines, before the full
traceback.

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
interpreter selected in the Python extension (VS Code 1.91 or later).
PyWeb must be installed in that environment (0.5 or later for the data
features); set `pyweb.server.command` to use a
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

