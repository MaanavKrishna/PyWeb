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

The MCP server, templates and agent files need `pyweb-stack` 0.2.0 or
later (`pip install -U pyweb-stack`). Install PyWeb, then register the
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
| `pyweb_new_app` | Creates an app from a template (`blank`, `counter`, `todo`, `blog`, `auth`, `chat`) with `AGENTS.md`, `CLAUDE.md` and a stylesheet. |
| `pyweb_check` | Compiles the app and runs security checks. Returns `ok`, errors with `line`, `message` and a fix `hint`, plus pages, signals, what's sent to the browser, JS size and the server functions. |
| `pyweb_inspect` | For every name: does it run in the browser or on the server, and why. |
| `pyweb_compiled` | The JavaScript generated for a page, and its server-rendered HTML. |
| `pyweb_render` | Requests a URL from the app: status, redirect target, HTML, and the Python traceback for 500s. |
| `pyweb_call` | Calls an `@server` function exactly like the browser does, returning the result or the typed error. Cookies persist, so an assistant can log in and then render a protected page. |

The server also exposes the guide and every template as resources
(`pyweb://guide`, `pyweb://templates/<name>`) and a `build_pyweb_app`
prompt that walks an assistant through scaffold → edit → check → verify.

### What a session looks like

1. The assistant reads `pyweb_guide`.
2. It scaffolds with `pyweb_new_app` (for example `template="blog"`).
3. It edits `app.pyweb` and calls `pyweb_check` after each change. An
   error comes back as
   `{"line": 8, "message": "'sqlite3' only exists on the server ...",
   "hint": "Move that logic into an @server function and call it from the handler."}`.
4. It confirms the result with `pyweb_render` (each page) and `pyweb_call`
   (each server function).
5. You run `pyweb dev app.pyweb` and try it.

`pyweb_render` and `pyweb_call` run your app's server code, as
`pyweb dev` would. Assistants generally ask before calling them.

## AGENTS.md and CLAUDE.md

`pyweb new myapp --template todo` writes:

```text
myapp/
  app.pyweb
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
