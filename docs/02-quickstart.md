# Quickstart

## Install

The package is published as `pyweb-framework`; you import it as `pyweb`
and the command is `pyweb`.

```bash
pip install pyweb-framework            # Python 3.10+; no other dependencies
pip install "pyweb-framework[all]"     # optional: Postgres, MySQL, Redis, cryptography, uvicorn
```

## Create and run an app

```bash
pyweb new hello
cd hello
pyweb dev app.pyweb          # http://localhost:8000, reloads on save
```

`pyweb new` writes a single `app.pyweb`:

```pyweb
from pyweb import App

app = App(title="Hello")


@app.page("/")
def Home():
    count = 0

    def increment():
        count += 1

    <main>
        <h1>Counter</h1>
        <button onclick={increment}>
            Count: {count}
        </button>
    </main>
```

What happened:

- `count` is read by the markup and changed by `increment`, so it became
  a **signal**. Clicking the button updates only that button's text.
- `increment` was compiled to a JavaScript function. Nothing is sent to
  the server when you click.
- The first response was complete HTML (`Count: 0`), rendered on the
  server.

Edit the file and save: the dev server recompiles and the browser
reloads. If you introduce an error, the page shows it with the line.

## See what the compiler decided

```bash
pyweb inspect app.pyweb
```

```text
page Home("/") signals ['count'] rpc []
  place count: browser  # reactive state: assigned in increment(); literal initial value
  place increment: browser  # event handler (compiled to JavaScript)
```

## Check, build and serve

```bash
pyweb check app.pyweb                          # compile + security checks (CI-friendly)
pyweb build app.pyweb --out dist --production  # hashed, minified, deployable dist/
pyweb serve dist                               # production server with /healthz
```

`dist/` contains your app source, static assets and a `Dockerfile`; see
[Deployment](12-deployment.md).

## Next

The [tutorial](03-tutorial.md) builds a notes app with a database,
server functions and login.
