<div align="center">

# PyWeb

**Full-stack web apps in one Python file.**

Server-rendered pages, a reactive UI compiled from Python, typed calls to
your server, Models with migrations, forms, accounts, live updates,
background jobs and one-command deploys, with no JavaScript toolchain.

[![CI](https://github.com/MaanavKrishna/PyWeb/actions/workflows/ci.yml/badge.svg)](https://github.com/MaanavKrishna/PyWeb/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pyweb-stack)](https://pypi.org/project/pyweb-stack/)
![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-3776ab)
[![Docs](https://img.shields.io/badge/docs-maanavkrishna.github.io%2FPyWeb-0d9488)](https://maanavkrishna.github.io/PyWeb/)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

**[Documentation](https://maanavkrishna.github.io/PyWeb/)** ·
**[Try it in your browser](https://maanavkrishna.github.io/PyWeb/playground.html)** ·
**[Examples](#examples)** ·
**[Changelog](CHANGELOG.md)**

<br>

<img src="https://raw.githubusercontent.com/MaanavKrishna/PyWeb/main/docs/images/demo.gif" alt="A guestbook app written in one .pyweb file: the submit handler runs in the browser and sign() runs on the server" width="860">

</div>

```pyweb
from pyweb import App, server

app = App(title="Guestbook")
ENTRIES = []


@server
def sign(name: str) -> list:
    ENTRIES.append(name.strip() or "anonymous")
    return ENTRIES


@app.page("/")
def Home():
    entries = list(ENTRIES)     # computed on the server per request
    name = ""                   # bound to the input: browser state

    def submit():               # compiled to JavaScript
        entries = sign(name)    # typed RPC to the server
        name = ""

    <main>
        <h1>Guestbook ({len(entries)})</h1>
        <form onsubmit={submit}>
            <input bind={name} placeholder="Your name" />
            <button>Sign</button>
        </form>
        <ul>
            for entry in entries:
                <li>{entry}</li>
        </ul>
    </main>
```

```bash
pip install pyweb-stack      # imported as `pyweb`
pyweb dev app.pyweb          # http://localhost:8000, with live reload
```

That's the whole app. The page is rendered on the server with real data,
the handler becomes a few hundred bytes of JavaScript, and `sign()` becomes
a validated JSON endpoint the browser calls for you.

## What you get

### A whole product in one file

```pyweb
from pyweb import App, Field, Form, Input, Model, Submit, server
from pyweb.authkit import User

app = App(title="Feedback", database="sqlite:///app.db")
auth = app.use_auth()            # sign-up, passkeys, OAuth, /admin


class Idea(Model):
    text: str = Field(min=3, max=280)
    votes: int = 0
    author: User = Field(readonly=True)


def own(user, idea):
    return user is not None and idea.author_id == user.id


Idea.policy(write=own)           # only its author can change an idea


@server(login=True)
def share(idea: Idea):           # the form's fields come from Idea
    idea.author = auth.user()
    idea.save()
    thank.enqueue(idea.id)       # runs after the request commits


@app.job(retries=3)
def thank(idea_id: int):
    print(f"thanks for idea {idea_id}")


@app.page("/", login=True)
def Ideas():
    ideas = Idea.query().order("-id").live()    # every open tab

    <Form action={share}>
        <Input name="text" placeholder="Your idea" />
        <Submit>Share</Submit>
    </Form>
    <ul>
        for idea in ideas:
            <li>{idea["text"]} ({idea["votes"]})</li>
    </ul>
```

That's accounts (sign-up with email confirmation, passkeys, OAuth, an
admin), a table with rules and a row policy, a form that checks what
people type in the browser and again on the server (and works without
JavaScript), a background job queued in the same transaction, and a list
that updates in every open tab when anyone shares an idea. Start a bigger
one with `pyweb new myapp --template saas`, or follow
[a SaaS in an hour](docs/28-saas-tutorial.md).

### Python on both sides, split by the compiler

You write one file. The compiler decides, line by line, what runs in the
browser and what stays on the server, and tells you why
(`pyweb inspect`). Variables your handlers change become reactive state;
everything else is computed on the server. Database handles, imports and
secrets can't leak into browser code: that's a compile error with a line
number, not a production incident.

### Data, with migrations that write themselves

```pyweb
from pyweb import App, Field, Model

app = App(database="sqlite:///shop.db")      # DATABASE_URL takes over in production


class Order(Model):
    item: str = Field(max=80)
    status: str = Field(default="new", choices=["new", "paid", "shipped"])


@app.page("/orders")
def Orders():
    orders = Order.query().order("-id").limit(20).live()

    <ul>
        for o in orders:
            <li>{o["item"]}: {o["status"]}</li>
    </ul>
```

Models work on SQLite, Postgres and MySQL, with relations loaded by
`include()` (an N+1 loop is an error while you develop), cursor
pagination, transactions per request and read replicas.
`pyweb db diff` writes the migration when a Model changes and
`pyweb db upgrade` applies it, under a lock so many servers can start at
once. `.live()` keeps the list in step with the database in every open
tab: when one row changes, one row is sent. Raw SQL works too
(`live(db, "select ...")`). [Data →](docs/09-data.md) ·
[Live data →](docs/21-live-data.md)

### AI features that stream

```pyweb
from pyweb import App, Markdown, server

app = App()


@server
def answer(question: str):
    for piece in ("**Streaming**", " from", " the", " server."):   # your model's tokens
        yield piece


@app.page("/")
def Ask():
    question = ""
    reply = ""
    stream = None

    async def ask():
        reply = ""
        stream = answer(question)
        async for piece in stream:
            reply += piece

    def stop():
        if stream:
            stream.cancel()

    <input bind={question} />
    <button onclick={ask}>Ask</button>
    <button onclick={stop}>Stop</button>
    <Markdown text={reply} />
```

`yield` on the server, `async for` in the browser. Stop really stops:
the generator on the server is closed, so the model call ends too.
`<Markdown>` is safe on model output. Start from
`pyweb new mychat --template ai-chat` (Anthropic, any OpenAI-compatible
server, or a built-in demo model). [Building AI apps →](docs/20-ai-apps.md)

### Real multi-page apps

```pyweb
from pyweb import App

app = App(title="Plant shop")


@app.layout
def Shell(children):
    cart = 0

    def add():
        cart += 1

    <nav><a href="/">Home</a> <a href="/plants">Plants</a> <button onclick={add}>Cart {cart}</button></nav>
    {children}


@app.page("/plants", description="Every plant we sell")
def Plants(light: str = "", max_price: int = 0):    # typed query parameters
    <h1>Plants</h1>
```

Layouts keep their state while links load without a full page reload
(with prefetching and scroll restore). Links to the current page are
marked automatically, every page gets its own title, description and
social tags, and 404/500 pages are written in `.pyweb` too.
[Layouts & navigation →](docs/19-layouts-navigation.md)

### npm packages, without Node.js

```bash
pyweb add chart.js/auto
```

```python
Chart = npm("chart.js/auto")
```

PyWeb downloads the package from the npm registry, checks its checksum,
keeps only the files the browser loads and pins them in `pyweb.lock`.
There's no `node_modules` and no bundler, and pages that don't use a
package don't load it. [npm packages →](docs/18-npm-packages.md)

### Ship it, then see what it does

```bash
pyweb deploy fly          # or docker, compose, k8s, render, railway
```

`pyweb deploy` reads your app and explains its plan: Postgres instead of
SQLite, a worker for jobs, Redis when there's more than one server,
migrations before the new version takes traffic. Then it writes the
files. In production every log line is JSON with the request id and
trace id, `/metrics` answers Prometheus, OpenTelemetry traces follow a
click into its server function, SQL and background job, and while you
develop a toolbar on every page shows each request's queries and flags
N+1 loops. [Scaling & deploying →](docs/25-scaling.md) ·
[Logs, metrics & tracing →](docs/26-observability.md)

### Small, fast and boring to run

- **Every page is server-rendered**: complete HTML on first paint, then
  hydrated in place. Pages without interactivity ship no JavaScript.
- **Small**: a typical interactive page is under 1 KB of code plus a
  ~14 KB (gzip) runtime that's cached across pages. Forms and live
  updates add a few KB, only on pages that use them.
- **Its own server**: `pyweb serve` handles HTTP/1.1 keep-alive and
  WebSockets on one asyncio loop per process, with `--workers N`
  processes. Sessions are signed cookies, so servers stay stateless
  behind any load balancer. uvicorn and other ASGI servers work too.
- **Secure defaults**: escaped output, a hashed Content Security Policy,
  CSRF and origin checks, row policies, rate limits, request size limits
  and `pyweb check --production` before you deploy.

## See it

| [Live dashboard](examples/dashboard/app.pyweb) | [Streaming AI chat](examples/ai-chat/app.pyweb) | [Multi-page site](examples/site/app.pyweb) |
|---|---|---|
| ![Sales dashboard: totals and a Chart.js bar chart that update live](https://raw.githubusercontent.com/MaanavKrishna/PyWeb/main/docs/images/dashboard.png) | ![AI chat with a streamed Markdown reply](https://raw.githubusercontent.com/MaanavKrishna/PyWeb/main/docs/images/ai-chat.png) | ![Plant shop with a navigation layout and a cart](https://raw.githubusercontent.com/MaanavKrishna/PyWeb/main/docs/images/site.png) |
| `live()` queries + Chart.js from npm | `yield` + Stop + `<Markdown>` | layout, query parameters, 404 page |

Or open the **[playground](https://maanavkrishna.github.io/PyWeb/playground.html)**:
it runs the real PyWeb, server functions included, in your browser.

## Quick start

```bash
pip install pyweb-stack
pyweb new myapp --template saas      # saas | blank | counter | todo | blog | auth | chat | ai-chat
cd myapp
pyweb dev app.pyweb                  # edit app.pyweb; the page reloads on save
```

Then:

```bash
pyweb check app.pyweb                # compile + security checks (CI-friendly exit codes)
pytest                               # new apps come with test_app.py
pyweb db diff                        # a migration for what changed in your Models
pyweb deploy compose                 # or fly, render, railway, k8s, docker
```

The [quickstart](docs/02-quickstart.md) and [tutorial](docs/03-tutorial.md)
take it from there.

## How it works

```text
                      app.pyweb
                          │
                  ┌───────┴────────┐  compiler: parses Python + markup, decides
                  ▼                ▼  where each name runs, checks the boundary
       ┌─────────────────┐  ┌─────────────────────────┐
       │  browser module │  │  server                 │
       │  signals, DOM   │  │  page bodies, @server   │
       │  updates, your  │  │  functions, database,   │
       │  handlers in JS │  │  sessions, secrets      │
       └────────┬────────┘  └────────────┬────────────┘
                └──── typed JSON RPC ────┘
          (streams over HTTP; live data over one WebSocket per tab)
```

The [architecture overview](ARCHITECTURE.md) goes through each stage.

## Build it with AI

PyWeb ships an MCP server so AI assistants can scaffold, check, inspect,
render, screenshot and test your app, add npm packages, map its routes,
and look at its data: the schema, read-only queries (row-limited, secrets
redacted), pending migrations, jobs and recent requests. Errors come back
as line numbers with fix hints:

```bash
claude mcp add pyweb -- pyweb mcp                                    # Claude Code
```

```json
{ "mcpServers": { "pyweb": { "command": "pyweb", "args": ["mcp"] } } }
```

(the JSON is for Cursor, Claude Desktop, VS Code, Windsurf and other MCP
clients). New projects include `AGENTS.md` and `CLAUDE.md`, and the docs
site publishes [`llms-full.txt`](https://maanavkrishna.github.io/PyWeb/llms-full.txt).
[AI assistants & MCP →](docs/17-ai-assistants.md)

## Editor support

The **[VS Code extension](editors/vscode)** adds highlighting, errors as
you type, hover that shows where code runs (and the signatures of npm
packages), completion that knows your Models' fields and query methods,
warnings for N+1 loops and form fields the action doesn't take, a code
lens that writes the migration when a Model changes, and go to
definition. Any other LSP editor can run `pyweb lsp`
([setup](docs/14-cli.md#editor-support)).

## Examples

Each runs with `pyweb dev examples/<name>/app.pyweb` and is tested in a
real browser by the test suite.

| Example | Shows |
|---|---|
| [`saas`](examples/saas/app.pyweb) | accounts, Models with migrations, row policies, forms, live lists, a job, a daily email, an admin |
| [`counter`](examples/counter/app.pyweb) | signals, computed values, binding a number input |
| [`todo`](examples/todo/app.pyweb) | components, list mutation, filters, keyed lists |
| [`blog`](examples/blog/app.pyweb) | a Model, server functions, route parameters, 404s, validation errors |
| [`auth`](examples/auth/app.pyweb) | your own registration, password hashing and sessions (or use `app.use_auth()`) |
| [`chat`](examples/chat/app.pyweb) | route parameters, shared server state, live updates with `publish`/`subscribe` |
| [`showcase`](examples/showcase/app.pyweb) | everything on one page, with a stylesheet |
| [`site`](examples/site/app.pyweb) | a layout, client-side navigation, query parameters, page titles, a 404 page |
| [`dashboard`](examples/dashboard/app.pyweb) | live queries and a Chart.js chart from npm, in every open window |
| [`ai-chat`](examples/ai-chat/app.pyweb) | streaming AI replies with Stop and Markdown (Anthropic, OpenAI-compatible or a demo model) |

## Documentation

| | |
|---|---|
| Start | [Introduction](docs/01-introduction.md) · [Quickstart](docs/02-quickstart.md) · [Tutorial](docs/03-tutorial.md) · [A SaaS in an hour](docs/28-saas-tutorial.md) · [AI assistants & MCP](docs/17-ai-assistants.md) |
| Language | [`.pyweb` files](docs/04-pyweb-files.md) · [State & reactivity](docs/05-reactivity.md) · [Python in the browser](docs/07-browser-python.md) · [npm packages](docs/18-npm-packages.md) |
| Server | [Server functions & RPC](docs/06-server-functions.md) · [Pages & routing](docs/08-pages-routing-assets.md) · [Layouts & navigation](docs/19-layouts-navigation.md) · [Data](docs/09-data.md) · [Forms](docs/23-forms.md) · [Auth](docs/10-auth.md) · [Live data](docs/21-live-data.md) · [Background jobs](docs/24-jobs.md) · [Building AI apps](docs/20-ai-apps.md) |
| Ship | [Testing](docs/11-testing.md) · [Deployment](docs/12-deployment.md) · [Scaling](docs/25-scaling.md) · [Logs, metrics & tracing](docs/26-observability.md) · [Security](docs/13-security.md) · [CLI](docs/14-cli.md) · [Upgrading to 0.5](docs/27-upgrading.md) |
| Reference | [Recipe: wallets & web3](docs/22-recipe-web3.md) · [Toolkit & stability](docs/15-toolkit.md) · [Limitations & roadmap](docs/16-limitations-roadmap.md) · [Architecture](ARCHITECTURE.md) · [Changelog](CHANGELOG.md) |

The same docs are published at
**[maanavkrishna.github.io/PyWeb](https://maanavkrishna.github.io/PyWeb/)**,
with compiler output shown next to each example.

## Is it for you?

**Good fit:** internal tools, admin panels, dashboards, CRUD apps, AI
features, small SaaS products and content sites with interactive parts,
built by people who'd rather stay in Python.

**Not a fit:** large client-heavy single-page apps built around a
JavaScript component framework (use React, Svelte or Vue), or running
scientific Python in the browser (use Pyodide or PyScript). See the
[introduction](docs/01-introduction.md) and the
[current limitations](docs/16-limitations-roadmap.md).

## Status

PyWeb is in beta (0.x). The language, server API, RPC protocol and CLI
are documented and tested, and changes to them are announced in the
[changelog](CHANGELOG.md) (see [stability](docs/15-toolkit.md#stability)).
Upgrade with `pip install -U pyweb-stack`.

| Version | Highlights |
|---|---|
| 0.5 | Models with migrations, forms, auth kit with passkeys, OAuth and an admin, row-level live updates over WebSockets on PyWeb's own server, durable jobs and schedules, `pyweb deploy` for docker, compose, k8s, fly, render and railway, logs, metrics, tracing and a dev toolbar, data-aware editor and MCP tools, the `saas` template, `pyweb upgrade` |
| 0.4 | npm packages without Node, layouts and client-side navigation, streaming server functions and `<Markdown>` for AI apps, live queries, page head tags, `.pyweb` error pages; 0.4.1: richer MCP tools; 0.4.2: VS Code run, new-app and MCP commands; 0.4.3: fixes, security hardening, gzip and caching; 0.4.4: key rotation, session revocation, typed arguments, Redis-shared limits, `check --production` |
| 0.3 | Hydration, live updates (SSE), multi-file apps, language server + VS Code extension, browser playground, faster rendering, screenshot/test MCP tools |
| 0.2 | MCP server for AI assistants, AI guide, project templates, `AGENTS.md`/`CLAUDE.md`, `llms.txt` |
| 0.1 | First public release: compiler, reactive runtime, server rendering, typed RPC, sessions, databases, CLI |

The test suite (about 1,200 tests) covers the parser, the
Python→JavaScript translation (differentially, against CPython), the
reactive runtime, server rendering, RPC, forms, Models and migrations
(with property tests), the HTTP and WebSocket parsers (fuzzed), live
patches, jobs on every backend, every example app in Chromium, the
playground on Pyodide, and the whole suite against real Postgres, MySQL
and Redis servers, on Python 3.10–3.13. CI also boots a
`pyweb deploy compose` stack and runs the VS Code extension in a real
VS Code.

## Contributing

Issues and pull requests are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).
Security reports: [SECURITY.md](SECURITY.md).

Built by [MaanavKrishna](https://github.com/MaanavKrishna). MIT licensed.
