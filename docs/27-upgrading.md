# Upgrading to 0.5

Most 0.4 apps run on 0.5 as they are. This page lists what changed and
what, if anything, to do about it. The first step is always:

```bash
pip install -U pyweb-stack
pyweb upgrade --check app.pyweb      # lists what to change, with file and line
pyweb upgrade --fix app.pyweb        # applies the safe rewrites
pyweb check app.pyweb
pytest
```

`PYWEB_STRICT_DEPRECATIONS=1` turns every deprecation warning into an
error. Use it in CI to find the last uses of old APIs.

## Data

**Models.** 0.4 Models keep working. Rows still read like dicts
(`row["name"]`) and as attributes (`row.name`). Two things change:

- `Model.configure("app.db")` is deprecated. Give the app its database
  instead (`app = App(database="sqlite:///app.db")`), and every Model uses
  it. In production, `DATABASE_URL` replaces that URL, so you never edit
  the code to deploy.
- In production, a Model with no database is an error. 0.4 quietly used
  an in-memory SQLite database, which loses everything on restart.

**Raw SQL.** `pyweb.db.connect(...)` and `db.execute(...)` are
unchanged. You can move tables to Models one at a time: `pyweb db adopt`
records the current schema as the first migration, then `pyweb db diff`
takes over.

**SQLite** now enforces foreign keys and uses write-ahead logging. An app
whose data already breaks a foreign key sees an error on the next write
to that row. `PRAGMA foreign_key_check` lists such rows.

**Migrations.** The 0.4 `.sql` migrations and their journal still work,
and can sit next to the new Python migrations that `pyweb db diff`
writes. `pyweb db migrate` and `rollback` are now `upgrade` and
`downgrade`; the old names still work.

## Server functions

Parameter rules are now enforced on every call. If a parameter is
annotated `Annotated[str, Field(max=40)]`, a call that breaks the rule
gets a 422 with the error for that field. 0.4 checked only the type.
Clients that sent values outside the rules now get an error instead of
silently passing.

## Jobs

`pyweb.jobs` became a package. `Queue`, `RedisQueue` and `@task` still
work, but they are in memory: a restart loses queued work. Durable jobs
replace them:

```python
@app.job(retries=5)
def send_invoice(order_id: int): ...

send_invoice.enqueue(order.id)       # was: queue.enqueue(send_invoice, order.id)
```

`pyweb serve` runs them in the same process unless you run
`pyweb worker` separately (then set `PYWEB_WORKER=0` on the web
processes). See [Background jobs](24-jobs.md).

## Serving and deploying

- `pyweb serve` is PyWeb's own server: WebSockets, keep-alive, and
  `--workers N` processes. It listens on `$PORT` when set (every
  platform sets it). `pyweb serve --app module:factory` keeps the old
  threaded server.
- `PYWEB_MAX_CONNECTIONS` defaults to 10,000 per process (it was 256).
- `pyweb deploy TARGET` replaces `pyweb deploy --target T --compose`. The
  old flags still work. Run `pyweb deploy compose --plan` to see what the
  new planner would write: it adds a worker, Postgres or Redis when your
  app needs them. Regenerate your files rather than editing the old ones.

## Requests and responses

- `X-Request-Id` is now the W3C trace id (32 hex characters), the same id
  your logs and traces carry. Behind a proxy you trust
  (`PYWEB_TRUST_PROXY`), an incoming `X-Request-Id` is kept.
- `/metrics` and `/__pyweb/metrics` answer Prometheus scrapes. In
  production they need `PYWEB_METRICS_TOKEN`; without it they are a 404.
  If your app has its own page at `/metrics`, your page still wins.
- Logs are JSON lines in production (`PYWEB_LOG_FORMAT=text` keeps
  readable lines). The older `pyweb.observability` classes still work.

## Security changes

- **Tokens are signed with purpose keys only.** 0.4.4 still accepted
  sessions and feed tokens signed with the raw `PYWEB_AUTH_SECRET`, so that
  upgrading from 0.4.3 signed nobody out. 0.5 doesn't. Visitors whose
  session cookie dates from 0.4.3 or earlier sign in once more. If your
  code calls `auth.issue_session(data, secret)` itself, pass
  `pyweb.keys.derive(secret, "session")` instead (or use
  `session.login()`). `pyweb upgrade --check` points at those calls.
- **Stricter Content-Security-Policy.** Inline `<style>` blocks are
  allowed by their hash instead of `'unsafe-inline'`, and forms may post
  only to your own site (`form-action 'self'`). `style="..."` attributes
  still work. If a page posts a form to another site (a payment provider,
  say), add that site to `PYWEB_CSP`.
- **`pyweb check`** also warns about SQL built from strings, pages that
  read `auth.user().name` without `login=True`, and Models that belong to
  a user but have no row policy. `pyweb check --strict` fails on any
  warning.

## Accounts

Apps that keep their own login code (like the 0.4 `auth` example) are
unaffected. To move to the auth kit, `app.use_auth()` adds the users
table, sign-in pages and `/admin`. The session cookie and its secret are
the same, but the kit identifies people by user id, so sessions your own
code started (usually with an email as the id) don't match an account:
people sign in once more. In production the kit needs
`PYWEB_ORIGIN` (the site's address) for the links in its emails.
`pyweb check --production` tells you if it's missing.

## Editors and assistants

Update the VS Code extension to 0.5 for the data features (Model
completion, form fields, N+1 and migration warnings). MCP clients pick up
the new tools (`pyweb_db_schema`, `pyweb_db_query`, `pyweb_migrations`,
`pyweb_jobs`, `pyweb_requests`) the next time they start `pyweb mcp`.
