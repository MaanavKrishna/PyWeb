# Introduction

PyWeb lets you build a complete web application in Python, in one file.
You write pages as Python functions with HTML-like markup, keep
interactive state in ordinary local variables, and mark the functions
that must run on the server with `@server`. The compiler works out what
runs where and produces:

- **server-rendered HTML** for every page, so the first paint is complete
  and works for search engines and slow devices;
- **a small JavaScript module per interactive page**, compiled from your
  Python event handlers and expressions (no Python runtime in the browser);
- **typed RPC endpoints** for your `@server` functions, with generated
  browser calls, argument validation and structured errors.

Around that core, PyWeb has what a product needs, in the same file:

- **Data**: Models with relations, a query builder, migrations that
  write themselves (`pyweb db diff`) and row policies that decide who
  sees which rows. SQLite, Postgres and MySQL.
  ([Data & databases](09-data.md))
- **Forms**: `<Form action={create_post}>` takes its fields and rules from
  the Model, checks them in the browser as you type and again on the
  server, and works without JavaScript. ([Forms & uploads](23-forms.md))
- **Accounts**: `app.use_auth()` adds sign-up, login, passkeys, magic
  links, OAuth, two-factor and an admin. ([Authentication](10-auth.md))
- **Live updates**: `Post.query().live()` keeps a list in step with the
  database in every open tab, sending only the rows that changed.
  ([Live data](21-live-data.md))
- **Background work**: `@app.job` and `@app.cron`, stored in your
  database, retried, and queued in the same transaction as the request.
  ([Background jobs](24-jobs.md))
- **Shipping**: `pyweb deploy` writes Docker, Compose, Kubernetes, Fly,
  Render or Railway files; logs, metrics and traces come built in.
  ([Scaling & deploying](25-scaling.md))

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
    entries = list(ENTRIES)     # runs on the server for each request
    name = ""                   # bound to the input below: browser state

    def submit():               # compiled to JavaScript
        entries = sign(name)    # calls the server over RPC
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

Run it with `pyweb dev app.pyweb` and open <http://localhost:8000>.

## The problem it solves

Python developers who need a real web UI usually pick one of these:

| Approach | What you write | The cost |
|---|---|---|
| Django / Flask / FastAPI + a JS framework | Python API **and** a React/Vue/Svelte app | Two languages, two builds, a hand-written API layer between them |
| Server templates + htmx | Python views, HTML templates, endpoints per interaction | Every interaction is a server round trip; client state is awkward |
| Server-driven Python UI (Reflex, NiceGUI, Streamlit) | Python only | UI state lives on the server; each click is a network round trip over a WebSocket, and servers hold per-user sessions |
| Python in the browser (PyScript / Pyodide) | Python only | A multi-megabyte runtime download before anything is interactive |

PyWeb takes the architecture of modern JavaScript meta-frameworks
(server rendering + fine-grained client reactivity + server functions)
and makes Python the source language for all of it:

- **Interactions run in the browser.** Typing, toggling and filtering
  update the DOM locally. The network is used only when your code calls
  a `@server` function, or when a page shows live data.
- **Servers stay stateless.** Pages render per request, RPC calls are
  plain JSON over HTTP and sessions are signed cookies, so any number of
  processes can sit behind an ordinary load balancer without sticky
  sessions. Pages with live data open one WebSocket per tab (Server-Sent
  Events where WebSockets are blocked), and Redis carries changes between
  processes.
- **What ships is small.** The shared runtime is about 14 KB gzipped and
  cached; a typical page adds well under 1 KB. Forms (about 3 KB) and
  live updates (about 2 KB) load only on pages that use them, and pages
  without interactivity ship no JavaScript at all.
- **Boundaries are explicit and checked.** Code that can't run in a
  browser (database access, imports, secrets) is a compile error with a
  file and line, not a runtime surprise. `pyweb inspect` explains where
  every name runs and why.

## When PyWeb is a good fit

- Internal tools, admin panels, dashboards and CRUD apps, including
  ones that update live as the database changes.
- AI features: chat, summarising, drafting, with replies streamed from
  your server and keys kept there.
- Product sites and SaaS apps with accounts, data and background work
  (`pyweb new myapp --template saas` starts one; the
  [SaaS tutorial](28-saas-tutorial.md) walks through it).
- Teams that know Python and want a web UI without adopting a separate
  JavaScript stack.

## When to choose something else

- **Large single-page apps** built around a JavaScript component
  framework (React/Svelte/Vue component libraries): use that framework
  directly. Plain npm libraries (charts, maps, editors) work in PyWeb;
  see [npm packages](18-npm-packages.md).
- **Scientific Python in the browser** (NumPy, pandas client-side): use
  Pyodide/PyScript. PyWeb compiles a defined subset of Python to
  JavaScript; it does not run CPython in the browser.
- **Notebook-style data apps** where a script re-running on every
  interaction is the desired model: Streamlit is purpose-built for that.

## How it works, briefly

1. **Parse.** A `.pyweb` file is Python plus markup statements. The parser
   separates the two, keeping line numbers exact for both.
2. **Classify.** For each page, every local variable is classified:
   *signal* (changed by an event handler or bound to an input),
   *computed* (derived from signals), or *constant*; and by where its
   first value comes from: a literal, the browser, or the server.
3. **Place.** Event handlers and markup expressions are compiled to
   JavaScript. Calls to `@server` functions become awaited RPC calls.
   Everything else stays on the server.
4. **Render.** On each request the server runs the page function, renders
   HTML, and embeds only the values the browser code reads as JSON.
5. **Hydrate.** The page's module adopts the server-rendered DOM and
   attaches live bindings to it; from then on, each signal update touches
   only the nodes that depend on it.

Read on: [Quickstart](02-quickstart.md) · [Tutorial](03-tutorial.md) ·
[The .pyweb language](04-pyweb-files.md).
