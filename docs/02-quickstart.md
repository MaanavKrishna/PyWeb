# Quickstart

To try PyWeb without installing anything, open the
[playground](https://maanavkrishna.github.io/PyWeb/playground.html): it
runs the compiler, server rendering and your `@server` functions in the
browser.

## Install

The package is published as `pyweb-stack`; you import it as `pyweb`
and the command is `pyweb`.

```bash
pip install pyweb-stack            # Python 3.10+; no other dependencies
pip install "pyweb-stack[all]"     # optional: Postgres, MySQL, Redis, argon2, OpenTelemetry, ...
```

## Create and run an app

```bash
pyweb new hello
cd hello
pyweb dev app.pyweb          # http://localhost:8000, reloads on save
```

`pyweb new` writes `app.pyweb`, a starter test (`test_app.py`) and
`AGENTS.md`/`CLAUDE.md` (instructions for AI coding agents). Add
`--template saas` for a small product with accounts, a database,
migrations, live updates, a background job and an admin, or `todo`,
`blog`, `auth`, `chat`, `ai-chat` or `blank`. The default counter app is:

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
server functions and login, and [a SaaS in an hour](28-saas-tutorial.md)
builds a product with accounts, live updates and background jobs, then
ships it. Then, depending on what you're building:

- tables, relations and migrations: [Data & databases](09-data.md);
- forms that check what people type: [Forms & uploads](23-forms.md);
- sign-up, login, passkeys and an admin: [Authentication](10-auth.md);
- work that runs later or on a schedule: [Background jobs](24-jobs.md);

- several pages with a shared header: [Layouts & navigation](19-layouts-navigation.md);
- pages that update when data changes: [Live data](21-live-data.md);
- chat or other AI features: [Building AI apps](20-ai-apps.md), or start
  from `pyweb new myapp --template ai-chat`;
- charts, maps or editors from npm: [npm packages](18-npm-packages.md);
- working with an AI assistant: [AI assistants & MCP](17-ai-assistants.md).
