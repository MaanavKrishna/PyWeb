# Building with AI assistants

PyWeb is designed to be easy for AI coding tools to write correctly: one
file per app, plain Python, and a compiler that rejects mistakes with a
line number instead of failing at runtime. Three things help assistants
get it right:

1. an **MCP server** (`pyweb mcp`) that gives any MCP-capable assistant
   tools to scaffold, check, inspect, render and test apps;
2. **`AGENTS.md` / `CLAUDE.md`** written into every new project, so
   agents follow PyWeb's rules even without MCP;
3. **`llms.txt`** on the docs site for tools that read documentation.

## The MCP server

The tools below are those of `pyweb-stack` 0.4.1
(`pip install -U pyweb-stack`; older versions have fewer:
`pyweb_routes`, `pyweb_packages` and the task prompts arrived in 0.4.1,
`pyweb_screenshot` and `pyweb_test` in 0.3.0). Install PyWeb, then register the
server with your assistant. It talks
over stdio and has no dependencies beyond PyWeb itself.

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

### Tools

| Tool | What it does |
|---|---|
| `pyweb_guide` | Returns the rules for writing `.pyweb` apps (or one section): what runs where, state, markup, components, server functions, the browser Python subset, and every common error with its fix. |
| `pyweb_new_app` | Creates an app from a template (`blank`, `counter`, `todo`, `blog`, `saas`, `auth`, `chat`, `ai-chat`) with `AGENTS.md`, `CLAUDE.md`, tests and a stylesheet; apps with a database also get their first migration. `saas` (accounts, Models with row policies, forms, live pages, jobs, admin) is the best start for a product. |
| `pyweb_check` | Compiles the app and runs security checks. Returns `ok`, errors with `line`, `message` and a fix `hint`, plus pages (with their layouts, live-data variables and npm packages), signals, what's sent to the browser, JS size and the server functions (marking those that stream). |
| `pyweb_inspect` | For every name: does it run in the browser or on the server, and why. |
| `pyweb_compiled` | The JavaScript generated for a page, and its server-rendered HTML. |
| `pyweb_render` | Requests a URL from the app: status, redirect target, title and head tags, the layouts and page that rendered, HTML, and the Python traceback for 500s. |
| `pyweb_call` | Calls an `@server` function exactly like the browser does, returning the result (or every streamed chunk, for functions that `yield`) or the typed error. Cookies persist, so an assistant can log in and then render a protected page. |
| `pyweb_routes` | The map of the app: every page with its route, parameters (path or query string, with types and defaults), title and head tags, the layouts around it and its live-data variables; layouts, error pages, and server functions. |
| `pyweb_packages` | Adds, removes or lists npm packages for browser code (no Node.js): returns versions, exported names from the package's TypeScript declarations, and the `npm(...)` lines to bind them. Adding downloads from the npm registry. |
| `pyweb_screenshot` | Opens a page in headless Chromium, optionally runs steps (click, fill, press, select, goto, wait), and returns a screenshot plus the page text, console errors and whether the page hydrated. Needs Playwright (`pip install playwright && python -m playwright install chromium`). |
| `pyweb_db_schema` | Every Model (fields, types, rules, relations, policies), the tables the database has now, and the drift between them (what `pyweb db diff` would write). |
| `pyweb_db_query` | One read-only SQL statement (`SELECT`, `WITH`, `EXPLAIN`) on the app's database, capped at 1,000 rows, with secrets redacted. Writes are refused, even hidden in a CTE, and the transaction is rolled back. |
| `pyweb_migrations` | `status`, `diff` (write the next migration from the Models; removals go to a separate contract step) or `upgrade` (apply them to the development database). |
| `pyweb_jobs` | Background jobs with their errors and counts; retry failed ones, or run what's queued now to test a job end to end. |
| `pyweb_requests` | What the last `pyweb_render`/`pyweb_call` requests did: time, status, every SQL statement, N+1 warnings, spans, jobs queued, emails and errors. |
| `pyweb_test` | Runs the app's pytest tests and returns pass/fail counts, the summary and the failure output. |

The server also exposes the guide and every template as resources
(`pyweb://guide`, `pyweb://templates/<name>`) and three prompts:
`build_pyweb_app` (scaffold → edit → check → verify), `add_ai_feature`
(a streaming AI feature with Stop and Markdown in an existing app) and
`make_data_live` (switch pages to live queries).

### What a session looks like

1. The assistant reads `pyweb_guide`.
2. It scaffolds with `pyweb_new_app` (for example `template="blog"`).
3. It edits `app.pyweb` and calls `pyweb_check` after each change. An
   error comes back as
   `{"line": 8, "message": "'sqlite3' only exists on the server ...",
   "hint": "Move that logic into an @server function and call it from the handler."}`.
4. It confirms the result with `pyweb_render` (each page) and `pyweb_call`
   (each server function), and looks at the page with `pyweb_screenshot`,
   clicking and typing through the flow it built.
5. When it changes a Model, it writes and applies the migration with
   `pyweb_migrations`, checks rows with `pyweb_db_query`, and looks at
   `pyweb_requests` for slow or repeated queries.
6. It adds tests to `test_app.py` (`client.login("ann@example.com")` signs
   a test in) and runs them with `pyweb_test`.
7. You run `pyweb dev app.pyweb` and try it.

`pyweb_render`, `pyweb_call`, `pyweb_screenshot`, `pyweb_jobs` and `pyweb_test` run
your app's code, as `pyweb dev` or `pytest` would, and `pyweb_packages`
writes files and downloads packages, and `pyweb_migrations` writes and
applies migrations to the development database. Assistants generally ask before
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
