# Building with AI assistants

PyWeb is designed to be easy for AI coding tools to write correctly: one
file per app, plain Python, and a compiler that rejects mistakes with a
line number instead of failing at runtime. Three things help assistants
get it right:

1. an **MCP server** (`pyweb mcp`) that gives any MCP-capable assistant
   tools to scaffold, check, run, look at, debug and ship apps, and lets
   it see your running `pyweb dev`;
2. **`AGENTS.md` / `CLAUDE.md`** written into every new project, so
   agents follow PyWeb's rules even without MCP;
3. **`llms.txt`** on the docs site for tools that read documentation.

## The MCP server

The server speaks MCP 2025-11-25 (and the older versions clients still
use) over stdio, or over Streamable HTTP with `pyweb mcp --http`. It needs
nothing beyond PyWeb. Install PyWeb, then register it with your assistant.

**Claude Code**

```bash
claude mcp add pyweb -- pyweb mcp
```

**Cursor** (`.cursor/mcp.json`), **Claude Desktop**
(`claude_desktop_config.json`), **Windsurf**, **VS Code** and other MCP
clients use the same shape:

```json
{
  "mcpServers": {
    "pyweb": { "command": "pyweb", "args": ["mcp"] }
  }
}
```

If `pyweb` isn't on the assistant's `PATH` (for example, it lives in a
virtual environment), use the full path to the executable, or
`"command": "python", "args": ["-m", "pyweb.cli", "mcp"]` with that
environment's Python.

Paths given to the tools are relative to the directory the server starts
in, which is normally your project.

**Over HTTP.** Clients that connect by URL can use

```bash
pyweb mcp --http                    # http://127.0.0.1:8765/mcp
```

and register `http://127.0.0.1:8765/mcp` as a Streamable HTTP server. It
listens only on this machine and refuses requests from web pages (their
`Origin`) and other host names (DNS rebinding). To listen on another
address, give it a token (`--token`, or `PYWEB_MCP_TOKEN`); clients then
send `Authorization: Bearer <token>`.

### Tools

Every tool has a title, says whether it only reads or also changes
things, and describes its result with a schema, so assistants know what
they'll get back. Wrong arguments come back as a clear error before the
tool runs. Slow tools (tests, screenshots, profiling) report progress.

**Writing the app**

| Tool | What it does |
|---|---|
| `pyweb_guide` | The rules for writing `.pyweb` apps (or one section): what runs where, state, markup, components, server functions, the browser Python subset, and every common error with its fix. |
| `pyweb_docs` | Searches the documentation (every page, the guide and the threat model) and returns the matching sections, or one whole page. For any "how do I..." question. |
| `pyweb_new_app` | Creates an app from a template (`blank`, `counter`, `todo`, `blog`, `saas`, `auth`, `chat`, `ai-chat`, `desktop`) with `AGENTS.md`, `CLAUDE.md`, tests and a stylesheet; apps with a database also get their first migration. `saas` (teams, accounts, tenant Models, forms, live pages, jobs, webhooks, admin) is the best start for a product; `desktop` is a notes app for `pyweb desktop`. |
| `pyweb_check` | Compiles the app and runs the security and accessibility checks. Returns `ok`, errors with `line`, `message`, a fix `hint`, a code `frame` and a `docs` link, plus pages (with their layouts, live-data variables and npm packages), signals, what's sent to the browser, JS size and the server functions. |
| `pyweb_explain` | What your editor shows for a line: for each name, where it runs and why (or its signature or npm type), the problems on that line and, with `complete`, the completions at a column. |
| `pyweb_inspect` | For every name: does it run in the browser or on the server, and why. |
| `pyweb_compiled` | The JavaScript generated for a page, and its server-rendered HTML. |
| `pyweb_routes` | The map of the app: every page with its route, parameters (path or query string, with types and defaults), title and head tags, the layouts around it and its live-data variables; layouts, error pages, and server functions. |
| `pyweb_ui` | The UI kit: every component with its variants and sizes (`list`), one component's parameters, allowed values and source (`show`), a copy in your `components/` folder to change (`add`), and a `Theme(...)` from an accent, radius, font or preset with its contrast checked (`theme`; `save` writes it into `App(theme=...)`). |
| `pyweb_packages` | Adds, removes or lists npm packages for browser code (no Node.js): returns versions, exported names from the package's TypeScript declarations, and the `npm(...)` lines to bind them. Adding downloads from the npm registry. |

**Running it and looking at it**

| Tool | What it does |
|---|---|
| `pyweb_render` | Requests a URL from the app: status, redirect target, title and head tags, the layouts and page that rendered, HTML, and the Python traceback for 500s. `as_user` (with `roles`, `org` and `org_role`) renders it as a signed-in user, a member of that organization. |
| `pyweb_call` | Calls an `@server` function exactly like the browser does, returning the result (or every streamed chunk, for functions that `yield`) or the typed error. Cookies persist, so an assistant can log in and then render a protected page; it takes the same `as_user`, `roles`, `org` and `org_role`. |
| `pyweb_screenshot` | Opens a page in headless Chromium (signed in with `as_user`, light or dark), optionally runs steps (click, fill, press, select, goto, wait), and returns a screenshot plus the page text, console errors and whether the page hydrated. Needs Playwright (`pip install playwright && python -m playwright install chromium`). |
| `pyweb_review` | A design and accessibility review of a page: screenshots in light and dark at desktop and phone widths, axe-core's audit of each (downloaded once from npm, checksum verified), console errors, and the source's accessibility lint. |
| `pyweb_test` | Runs the app's pytest tests and returns pass/fail counts, the summary and the failure output. |
| `pyweb_profile` | Runs a page or server function many times under cProfile: mean, p50 and p95 time, its SQL, N+1 warnings, the slowest functions and the app's own code among them. |
| `pyweb_webhook_send` | Sends a correctly signed test delivery (GitHub, Stripe or Standard Webhooks, whatever the receiver expects) to one of the app's `@app.webhook` paths, runs the handler's job and reports its result or error; `bad_signature` checks that forgeries are refused. |

**Data**

| Tool | What it does |
|---|---|
| `pyweb_db_schema` | Every Model (fields, types, rules, relations, policies, tenant scoping), the tables the database has now, and the drift between them (what `pyweb db diff` would write). |
| `pyweb_db_query` | One read-only SQL statement (`SELECT`, `WITH`, `EXPLAIN`) on the app's database, capped at 1,000 rows, with secrets redacted. Writes are refused, even hidden in a CTE, and the transaction is rolled back. |
| `pyweb_migrations` | `status`, `diff` (write the next migration from the Models; removals go to a separate contract step) or `upgrade` (apply them to the development database). |
| `pyweb_jobs` | Background jobs with their errors and counts; retry failed ones, or run what's queued now to test a job end to end. |
| `pyweb_requests` | What requests did: time, status, every SQL statement, N+1 warnings, spans, jobs queued, emails and errors. By default the requests `pyweb_render`/`pyweb_call` made; `source="dev"` shows what the running `pyweb dev` served to your browser. |
| `pyweb_teams` | For apps with organizations: each organization's plan, members and roles, invitations, and how many rows of each tenant Model it has. |

**Your running dev server**

| Tool | What it does |
|---|---|
| `pyweb_dev` | Whether `pyweb dev` is running for this app: its URL, whether the app compiles right now (with the error and traceback), its pages and recent 500 errors. |
| `pyweb_doctor` | Project health, as `pyweb doctor`: each check with `status` (ok, warn, fail), `detail` and a `fix`; `production=true` adds the production settings. |
| `pyweb_logs` | The dev server's log lines (warnings, errors with tracebacks, slow queries, job failures); `since` returns only newer ones. |

When `pyweb dev` starts, it writes `.pyweb/dev.json` next to the app: its
address and a random token, readable only by you (the folder has its own
`.gitignore`), deleted when it stops. The MCP server finds it there and
asks the dev server for its state with that token; without it, the dev
server refuses, so web pages and other people on the machine can't read
your logs. `pyweb_screenshot` and `pyweb_review` also use the running dev
server when there is one, so they show exactly what you see.

**Shipping**

| Tool | What it does |
|---|---|
| `pyweb_security_check` | The security review: secrets in the source and next to it, SQL built from strings, Models without policies, tenant Models read with `.unscoped()`, malformed rate limits and roles; with `production: true` the production settings too. Returns `ok` (no blocking finding) and each finding with its line, and links the threat model. |
| `pyweb_upgrade` | What to change for this version of PyWeb, with file, line and how (`pyweb upgrade --check`); `fix` applies the safe rewrites. |
| `pyweb_deploy_plan` | What `pyweb deploy TARGET` would run and why (processes, database, Redis, worker, migrations, secrets to set) and what the production settings are missing, without writing files. |
| `pyweb_openapi` | The OpenAPI 3.1 document for the app's server functions, as `pyweb openapi` writes it (for API clients and API tokens). |

### Resources and prompts

Resources an assistant can read (and complete names for):
`pyweb://guide`; `pyweb://docs` and `pyweb://docs/<page>` (every
documentation page, e.g. `pyweb://docs/teams`); `pyweb://kit` and
`pyweb://kit/<Component>` (a kit component's source, parameters and
allowed values); `pyweb://threat-model`; and `pyweb://templates/<name>`.

Prompts are step-by-step workflows that name the tools to use:

| Prompt | Does |
|---|---|
| `build_pyweb_app` | Scaffold, write, check, verify and test a new app from a description. |
| `add_ai_feature` | A streaming AI feature with Stop and Markdown in an existing app. |
| `make_data_live` | Switch pages to live queries. |
| `add_teams` | Organizations, invitations, roles, tenant Models and isolation tests. |
| `make_installable` | Manifest, icons, offline page and, optionally, Web Push. |
| `make_desktop_app` | A desktop window, native file dialogs and an installer. |
| `security_review` | Review against the threat model, fix and test. |
| `upgrade_pyweb` | Findings, safe rewrites, migrations and tests for a new PyWeb version. |
| `design_pass` | Kit components, a theme and motion, reviewed in light, dark and on a phone. |
| `fix_failing_test` | Reproduce, find the cause in the app, fix, re-run. |
| `debug_page` | Errors, logs, requests and a profile for one page. |

### What a session looks like

1. The assistant reads `pyweb_guide`, and `pyweb_docs` for the features
   it needs.
2. It scaffolds with `pyweb_new_app` (for example `template="saas"`).
3. It edits `app.pyweb` and calls `pyweb_check` after each change. An
   error comes back as
   `{"line": 8, "message": "'sqlite3' only exists on the server ...",
   "hint": "Move that logic into an @server function and call it from the handler."}`.
4. It confirms the result with `pyweb_render` (each page) and `pyweb_call`
   (each server function), acting as different people with `as_user` and
   `org`, and looks at the pages with `pyweb_review` or `pyweb_screenshot`.
5. When it changes a Model, it writes and applies the migration with
   `pyweb_migrations`, checks rows with `pyweb_db_query`, and looks at
   `pyweb_requests` or `pyweb_profile` for slow or repeated queries.
6. It adds tests to `test_app.py` (`client.login("ann@example.com",
   org="Acme")` signs a test in as a member) and runs them with
   `pyweb_test`, then `pyweb_security_check`.
7. You run `pyweb dev app.pyweb` and try it. If something breaks while you
   click around, the assistant reads the error with `pyweb_dev` and
   `pyweb_logs` and fixes it.

`pyweb_render`, `pyweb_call`, `pyweb_screenshot`, `pyweb_review`,
`pyweb_profile`, `pyweb_webhook_send`, `pyweb_jobs` and `pyweb_test` run
your app's code, as `pyweb dev` or `pytest` would; `pyweb_packages`
writes files and downloads packages; `pyweb_migrations` writes and
applies migrations to the development database; and `pyweb_ui` with
`add` or `save` writes into your project. Assistants generally ask before
calling them.

## AGENTS.md and CLAUDE.md

`pyweb new myapp --template todo` writes:

```text
myapp/
  app.pyweb
  test_app.py    a starter test (pyweb.testing.TestClient); run with pytest
  AGENTS.md      the PyWeb rules and workflow for coding agents
  CLAUDE.md      "@AGENTS.md" (Claude Code imports it)
  static/app.css
  .gitignore
```

`AGENTS.md` is read by most coding agents (Codex, Cursor, Aider, Jules,
…) and `CLAUDE.md` by Claude Code. They contain the same guide the MCP
server serves, plus the commands to check and run the app.

## llms.txt

The docs site publishes:

- [`llms.txt`](https://maanavkrishna.github.io/PyWeb/llms.txt): an index of
  the documentation with one-line summaries;
- [`llms-full.txt`](https://maanavkrishna.github.io/PyWeb/llms-full.txt):
  the AI guide followed by every docs page, in one file.

Point a chat assistant at `llms-full.txt` when it can't run tools.

## Tips for prompting

- Say "use PyWeb" and mention that the app is a single `app.pyweb` file.
- Ask the assistant to run `pyweb check` (or `pyweb_check`) after every
  edit and to fix errors by line number.
- For anything touching a database, files, secrets or third-party APIs,
  ask for an `@server` function; handlers run in the browser.
- Ask for `pyweb inspect` output when you want to know what data reaches
  the browser.
- Name the feature you want: "a layout with a nav", "make this list live",
  "stream the answer with a Stop button", "add chart.js". The guide has
  a section for each, so the assistant uses the built-in way instead of
  writing polling or fetch code by hand.
