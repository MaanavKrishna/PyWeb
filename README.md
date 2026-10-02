# PyWeb

**Full-stack web apps in one Python file.** Server-rendered pages,
reactive browser UI compiled from Python, and typed calls to server
functions, without a JavaScript toolchain.

[![CI](https://github.com/MaanavKrishna/PyWeb/actions/workflows/ci.yml/badge.svg)](https://github.com/MaanavKrishna/PyWeb/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-maanavkrishna.github.io%2FPyWeb-0d9488)](https://maanavkrishna.github.io/PyWeb/)
![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-3776ab)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

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
pip install pyweb
pyweb dev app.pyweb        # http://localhost:8000
```

## Why PyWeb

- **Interactions run in the browser.** Handlers and expressions are
  compiled to small JavaScript modules; the server is only contacted when
  your code calls a `@server` function. No WebSocket per user, no
  multi-megabyte Python runtime in the browser.
- **Every page is server-rendered.** Complete HTML on first paint, real
  data from your database, good for SEO and slow devices.
- **You never write an API layer.** `@server` functions get endpoints,
  argument validation, typed errors and generated browser calls.
- **Plain variables are state.** The compiler sees which variables your
  handlers change and makes exactly those reactive; updates touch only
  the DOM nodes that read them.
- **Boundaries are checked.** Database handles, imports and secrets can't
  leak into browser code: it's a compile error with a line number.
  `pyweb inspect` explains where every name runs and why.
- **Stateless servers.** Signed-cookie sessions and plain HTTP RPC scale
  horizontally behind any load balancer. Deploy with `pyweb serve`,
  uvicorn/gunicorn (ASGI) or the generated Dockerfile.

A typical interactive page ships under 1 KB of page code plus a ~10 KB
(gzip) runtime that's cached across pages. Pages without interactivity
ship no JavaScript.

## Is it for you?

**Good fit:** internal tools, admin panels, dashboards, CRUD apps,
small SaaS products and content sites with interactive parts, built by
people who'd rather stay in Python.

**Not a fit:** large client-heavy single-page apps that need the npm
ecosystem (use React/Svelte/Vue), or running scientific Python in the
browser (use Pyodide/PyScript). See the
[comparison](https://maanavkrishna.github.io/PyWeb/introduction.html)
and [current limitations](docs/16-limitations-roadmap.md).

## Documentation

| | |
|---|---|
| Start | [Introduction](docs/01-introduction.md) · [Quickstart](docs/02-quickstart.md) · [Tutorial](docs/03-tutorial.md) |
| Language | [`.pyweb` files](docs/04-pyweb-files.md) · [State & reactivity](docs/05-reactivity.md) · [Python in the browser](docs/07-browser-python.md) |
| Server | [Server functions & RPC](docs/06-server-functions.md) · [Pages & routing](docs/08-pages-routing-assets.md) · [Data](docs/09-data.md) · [Auth](docs/10-auth.md) |
| Ship | [Testing](docs/11-testing.md) · [Deployment](docs/12-deployment.md) · [Security](docs/13-security.md) · [CLI](docs/14-cli.md) |
| Reference | [Toolkit & stability](docs/15-toolkit.md) · [Limitations & roadmap](docs/16-limitations-roadmap.md) · [Architecture](ARCHITECTURE.md) · [Changelog](CHANGELOG.md) |

The same docs are published at
**[maanavkrishna.github.io/PyWeb](https://maanavkrishna.github.io/PyWeb/)**,
with compiler output shown next to each example.

## Examples

Each runs with `pyweb dev examples/<name>/app.pyweb` and is exercised in
a real browser by the test suite.

| Example | Shows |
|---|---|
| [`counter`](examples/counter/app.pyweb) | signals, computed values, binding a number input |
| [`todo`](examples/todo/app.pyweb) | components, list mutation, filters, keyed lists |
| [`blog`](examples/blog/app.pyweb) | SQL database, server functions, route params, 404s, validation errors |
| [`auth`](examples/auth/app.pyweb) | registration, password hashing, sessions, protected pages |
| [`chat`](examples/chat/app.pyweb) | route params, shared server state, polling with `on_mount` |
| [`showcase`](examples/showcase/app.pyweb) | everything on one page, with a stylesheet |

## Command line

```bash
pyweb new myapp                                  # scaffold
pyweb dev app.pyweb                              # dev server: live reload + error overlay
pyweb inspect app.pyweb                          # where each name runs, and why
pyweb check app.pyweb                            # compile + security checks for CI
pyweb build app.pyweb --out dist --production    # self-contained, hashed, minified dist/
pyweb serve dist                                 # production server (/healthz, CSP, graceful shutdown)
```

## Status

PyWeb 1.0 has a stable language, server API, RPC protocol and CLI (see
[stability](docs/15-toolkit.md#stability)). The test suite covers the
parser, the Python→JavaScript translation (differentially, against
CPython), the reactive runtime, server rendering, RPC, sessions, every
example app in Chromium, and the database/Redis layers against real
Postgres, MySQL and Redis servers, on Python 3.10–3.13.

## Authors

Built by [MaanavKrishna](https://github.com/MaanavKrishna) and Claude.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports:
[SECURITY.md](SECURITY.md). License: MIT.
