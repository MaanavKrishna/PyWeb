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
  a `@server` function.
- **Servers stay stateless.** Pages render per request and RPC calls are
  plain JSON over HTTP, so any number of processes can sit behind an
  ordinary load balancer. No sticky sessions or WebSocket fan-out.
- **What ships is small.** The shared runtime is about 10 KB gzipped and
  cached; a typical page adds well under 1 KB. Pages without
  interactivity ship no JavaScript at all.
- **Boundaries are explicit and checked.** Code that can't run in a
  browser (database access, imports, secrets) is a compile error with a
  file and line, not a runtime surprise. `pyweb inspect` explains where
  every name runs and why.

## When PyWeb is a good fit

- Internal tools, admin panels, dashboards and CRUD apps.
- Product sites and small SaaS apps where pages should be fast and
  indexable but still interactive.
- Teams that know Python and want a web UI without adopting a separate
  JavaScript stack.

## When to choose something else

- **Large single-page apps** with heavy client-side logic and a need for
  the npm component ecosystem: use React/Svelte/Vue directly.
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
