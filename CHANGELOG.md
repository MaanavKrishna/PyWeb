# Changelog

All notable changes to this project are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [semantic versioning](https://semver.org).

## [Unreleased]

Work towards 0.5.0: production apps in one Python file.

### Added
- **Models on `pyweb.db`.** `class Post(Model): title: str = Field(max=120)`
  works on SQLite, Postgres and MySQL with the same code. Types include
  `Decimal`, timezone-aware `datetime`, `date`, `time`, JSON (`dict`/`list`),
  `bytes`, `UUID`, `Text`, `Email`, `URL` and `Slug`. `Field(...)` holds the
  column's schema and its validation rules (`min`, `max`, `pattern`,
  `choices`, `format`, `unique`, `index`, `private`, `auto_now_add`, ...);
  `@validates("field")` adds custom checks. Saving writes only changed
  columns; unique violations become field errors.
- **Relations.** `author: User` is a foreign key, `tags: list[Tag]` is
  many-to-many, `OneToOne(...)`, with reverse accessors (`user.posts`).
  `include("author", "tags", "author.profile")` loads related rows with one
  query per relation; reading a relation that wasn't included is an error
  while developing (so pages can't do N+1 queries) and a logged warning in
  production. Relation filters (`Post.author.has(...)`, `User.posts.any(...)`)
  use subqueries.
- **Query builder.** Immutable, lazy queries with `Post.views > 10`, `&`,
  `|`, `~`, keyword lookups (`title__icontains=`, `id__in=`, ...),
  `order`, slicing, `first/last/get/get_or_404`, `count/exists`,
  `values/pluck`, `aggregate` and `group_by` with `Count/Sum/Avg/Min/Max`,
  bulk `update/delete`, cursor pagination with `page()`, `get_or_create`,
  `upsert`, `bulk_create`, and `.live()`.
- **Transactions per request.** Every `@server` call is one transaction,
  started at its first write; `@atomic` / `@atomic(False)`. Nested
  `db.transaction()` blocks are savepoints. `ValidationError` from an RPC
  answers 422 with the errors per field.
- **Migrations.** `pyweb db diff` compares Models with the live database
  and writes Python migrations; `upgrade`, `downgrade`, `status`, `adopt`,
  `squash`, `seed`. Changes are split into *expand* steps (safe during a
  rolling deploy) and *contract* steps (`upgrade --contract`), including
  renames with `--rename table.old=new`. A lock shared by every server
  (Postgres advisory lock, MySQL `GET_LOCK`, or a lock row) makes
  `pyweb serve --migrate` safe with many servers. SQLite schema changes
  rebuild tables with foreign keys paused. `pyweb dev` applies pending
  migrations, and apps without migrations create tables on first use.
- **Forms.** `<Form action={save_post}>` with `<Input>`, `<Textarea>`,
  `<Select>`, `<Checkbox>`, `<FileInput>`, `<Submit>` and `<FormError>`
  builds labelled, accessible fields from the action's Model (or plain
  typed parameters). Rules are checked in the browser while typing, with
  the same messages as the server; the form submits without a page load and
  shows server errors next to each input, and still works as a plain POST
  without JavaScript (422 re-render with values, 303 on success). CSRF
  token, cross-site refusal, a one-time key per submit (double clicks run
  once), only the action's own fields are read, `values={row}` edits a row
  through a signed id, and `forms.carry()`/`restore()` for multi-step forms.
  The browser code (`forms.js`) only loads on pages with a form.
- **Uploads and storage.** `File(types=["image/*"], max_size="2MB")` fields
  and `UploadedFile` parameters; types checked from the file's bytes;
  `PYWEB_MAX_UPLOAD` (default 10MB); `PYWEB_STORAGE` is a local folder
  (served with a sandboxing CSP) or S3-compatible storage with presigned
  URLs and no SDK.
- **Auth kit.** `auth = app.use_auth()` adds `/signup`, `/login`,
  `/logout`, `/reset`, `/account` and `/admin`, rendered on the server and
  working without JavaScript. Sign in with a password, an emailed link, a
  passkey (also offered in the email field's autofill) or GitHub / Google /
  Microsoft (PKCE + `state`), with optional authenticator-app codes and
  recovery codes. Define a page at the same route to replace one;
  `auth.user()`, `auth.authenticate()`, `auth.login()`, `auth.set_roles()`,
  `auth.revoke()` ... for custom flows. Tables `users`, `auth_identities`,
  `auth_credentials`, `auth_tokens` and `auth_events` are ordinary Models.
- **Guards.** `login=True`, `roles=[...]` and `fresh=seconds` on
  `@app.page`, `@app.layout` and `@server`.
- **Row policies.** `Note.policy(read=..., write=...)` limits the rows each
  user can see and change, on every query and save in a request
  (`with system():` skips them on purpose).
- **Admin.** `/admin` lists, searches, edits and deletes rows of every
  Model for users with the `admin` role; private fields never appear; every
  change is in the audit log.
- **Account security.** scrypt (or argon2id with `pyweb-stack[auth]`)
  hashes upgraded at sign-in; breached-password check in production;
  guessing slowed down per account and per IP (shared through Redis) with no
  lockout; the same answer and timing for unknown emails; emailed links
  hashed, single-use (also under concurrent clicks) and short-lived;
  passkey challenges single-use on every server; password resets, password
  and role changes sign out other sessions, durably in the `users` table
  (and fail closed if the database can't be checked); emailed links are
  built from `PYWEB_ORIGIN`, never from the `Host` header, in production.
- **`pyweb.mail`.** `PYWEB_MAIL_URL` (SMTP/SMTPS) and `PYWEB_MAIL_FROM`;
  while developing, emails are printed and kept in `pyweb.mail.OUTBOX`.
- `pyweb check --production` reports `use_auth()` without `PYWEB_ORIGIN`.
- **PyWeb's own server.** `pyweb serve` and `pyweb dev` now run on
  `pyweb.net`: HTTP/1.1, WebSockets (RFC 6455) and Server-Sent Events on
  one asyncio loop per process, standard library only. Thousands of open
  live connections cost a coroutine each instead of a thread (the old
  server stopped at 256); page renders and RPCs run on a thread pool
  (`PYWEB_THREADS`). `--workers N` / `WEB_CONCURRENCY` forks processes
  sharing the port and replaces crashed ones. The parser refuses request
  smuggling vectors (CL+TE, repeated lengths, folded headers, bad chunk
  sizes, bare LF) and has timeouts for heads, bodies, idle keep-alive and
  slow readers; SIGTERM drains gracefully.
- **One WebSocket per tab for live updates.** Every `subscribe()` and live
  query on a page shares `/__pyweb/ws` (Server-Sent Events used one
  connection each, and browsers allow six per site), with jittered
  reconnects that resume where they stopped, and automatic fallback to
  Server-Sent Events, then polling. The socket checks `Origin`
  (`PYWEB_ALLOWED_ORIGINS` for others), re-checks the session every minute
  and closes when it's revoked, limits subscriptions, message size and
  rate, and doesn't buffer without limit for slow clients. Works in the
  ASGI adapter too.
- **Live queries send patches.** Rows with an `id` (or `key=`) travel as
  inserts, updates, deletes and moves: changing one row of a long list
  sends that row only, and the browser redraws only it. A page that missed
  a change gets the whole result once. `Model.query().live()` uses the
  primary key.
- **Presence.** `presence(name)` and `join(room, info, on_members,
  on_cast)`: who is on a page, and ephemeral casts (cursors, typing) to
  everyone else in the room, across processes with Redis.
- Feeds made while someone is signed in only work for that session.
- `live.js`: live-update code loads only on pages that use it.
- **Durable background jobs.** `@app.job(retries=, timeout=, queue=,
  unique_for=)` and `fn.enqueue(..., delay=, at=, key=)`. Jobs are stored
  in the app's database (`pyweb_jobs`), Redis (`PYWEB_JOBS=redis`) or memory,
  and queued inside the request's transaction (a rollback queues nothing).
  Workers claim with leases (`FOR UPDATE SKIP LOCKED` on Postgres/MySQL), so
  a crashed or killed worker's job runs again elsewhere; failures retry with
  jittered exponential backoff, then go `dead` (retry from `/admin` or
  `pyweb jobs retry`); timeouts; graceful SIGTERM hands running jobs back.
  `pyweb worker`, `pyweb jobs list|retry|purge|run`, and a worker inside
  `pyweb dev`/`pyweb serve` (`PYWEB_WORKER=0` to run them separately).
- **Schedules.** `@app.cron("0 3 * * *", tz=..., catchup=...)` with PyWeb's
  own cron parser (daylight saving handled) and `@app.every(minutes=5)`.
  Every server runs the scheduler and each slot is queued exactly once,
  with no leader election.
- **Email outbox.** With durable jobs, `pyweb.mail.send` queues delivery as
  a retried job that only leaves if the request committed.
- **Nightly cleanup** of finished jobs, expired sign-in links and old audit
  entries.
- `db.after_commit(fn)`: run code once the current transaction commits
  (dropped on rollback, including a rolled-back savepoint), and
  `db.after_end(fn)` for code that must run either way.
- Jobs stored in the app's database are marked done in the job's own
  transaction, so a job's database writes happen exactly once even if a
  worker crashes or loses its lease.
- **`pyweb deploy` plans the production topology.** It reads the app
  (database, jobs, live updates, sign-in, uploads, migrations), decides
  what production needs for the target and number of machines (Postgres
  instead of SQLite for several machines or disposable disks, Redis for
  more than one process, a separate worker, migrations once per deploy,
  object storage for uploads, the secrets to set), prints the plan with
  the reason for each decision, and writes the files. Targets: `docker`,
  `compose` (Caddy, load-balanced replicas, Postgres, Redis, worker,
  migrate step), `k8s` (web and worker Deployments, migrate init
  container, probes, PodDisruptionBudget, HorizontalPodAutoscaler,
  Ingress), `fly`, `render` and `railway`. `--plan` prints only;
  `--check` fails CI when the plan has warnings.
- **A production image**: multi-stage, non-root, cached pip installs,
  health check, `SIGTERM` drain, `PYTHON_IMAGE`/`PYWEB_SPEC`/
  `EXTRA_PACKAGES` build arguments.
- `DATABASE_URL` overrides the URL in `App(database=...)` (develop on
  SQLite, deploy on Postgres, same code). `pyweb serve` uses `$PORT`.
- `pyweb check --production` also reports Models without migrations,
  unapplied migrations, email without `PYWEB_MAIL_URL`, uploads without
  object storage, and jobs with `PYWEB_WORKER=0`.
- CI deploys a real stack (2 replicas, worker, Postgres, Redis, Caddy)
  with `pyweb deploy compose` and checks load balancing, jobs on the
  worker and live updates across replicas.
- **Read replicas** with `DATABASE_REPLICA_URL` (read-your-writes inside a
  request), slow query logging with query plans (`PYWEB_SLOW_QUERY_MS`),
  `pyweb.db.QUERY_HOOKS`, seeds (`pyweb.db.seeds`) and
  `pyweb.testing.Factory`.

- **Observability.** A dev toolbar on every page in `pyweb dev`: the SQL each
  request ran (repeated statements grouped, with an N+1 warning), spans,
  jobs queued, emails captured, errors, and a list of every request since;
  it never ships in production builds. JSON logs in production (readable
  text while developing) where every line carries the request id, trace
  id, route and user, and secrets (`password`, `token`, `api_key`, ... plus
  `PYWEB_LOG_REDACT`) are redacted, including inside messages and URLs.
  Prometheus `/metrics` (`PYWEB_METRICS_TOKEN`; requests, server functions,
  queries, jobs, live sockets, mail, sign-ins, errors), added up across
  `--workers` processes; `pyweb worker --metrics-port`. W3C `traceparent`
  is continued, returned on every response and carried into the jobs a
  request queues, so one trace covers the click and the job's work on
  another machine. OpenTelemetry spans for requests, queries, jobs and
  `with span("..."):` blocks (`pip install pyweb-stack[otel]`,
  `PYWEB_OTEL=1`). `@app.on_error` hooks receive every unhandled error with
  its request context.
- `pyweb worker --alive`: a health check for worker containers (the worker
  touches a heartbeat file after each round that reached its job store);
  compose and Kubernetes use it.

- **`pyweb new --template saas`**: accounts, projects and tasks with row
  policies, `<Form>`s, live pages, a background job, a weekday cron email,
  `/admin`, seeds, tests and a first migration, ready for `pyweb deploy`.
  Templates with a database get `migrations/0001_initial.py` and a README.
- `TestClient.login(email, roles=[...])` signs a test in (creating the
  account with the auth kit) without going through the sign-in form;
  `logout()` signs out.
- `seeds.py` sees the app's Models by name; `pyweb.models.model("Post")`
  looks one up anywhere.
- A server function parameter can take its rules from a default:
  `title: str = Field(min=1, max=120)` (same as `Annotated[str, Field(...)]`).
- `<Input name="project_id" type="hidden" />` is just the input (for ids the
  page sets with `values=`); `label=""` hides a field's label but keeps it as
  the input's `aria-label`.

- **Editors know your data.** The language server completes Model fields
  and query methods after `Post.`, keyword fields in `where(`/`create(`,
  relations in `include("`, columns in `order("`, `Field(` options and a
  `<Form>`'s fields in `name="`. It flags form fields the action doesn't
  have, loops that read a relation their query didn't `include()` (N+1,
  pointing at the line), and Models changed without a migration, with a
  code lens that writes it. VS Code: "Create Migration from Model Changes",
  "Apply Migrations", and snippets for Models, relations, form pages, live
  pages, jobs, schedules, row policies and `use_auth`.
- **`pyweb db check`**: fails (exit 1) when the Models changed without a
  migration, by applying the migrations to a scratch database, so it needs
  no real database. For CI.
- **MCP tools for data**: `pyweb_db_schema` (Models, tables, drift),
  `pyweb_db_query` (read-only SQL, capped, secrets redacted),
  `pyweb_migrations` (status, diff, upgrade), `pyweb_jobs` (list, retry,
  run) and `pyweb_requests` (each request's SQL, N+1 warnings, jobs, errors).

### Changed
- `PYWEB_MAX_CONNECTIONS` defaults to 10,000 per process (it was 256 for
  the threaded server, which `pyweb serve --app module:factory` still uses).
- `pyweb.testing.serve` runs PyWeb's server.
- SQLite connections enforce foreign keys and use write-ahead logging.
- `Model.configure()` is deprecated in favour of `App(database=...)` /
  `DATABASE_URL`; 0.4-style Models keep working (rows still read like
  dicts: `row["name"]`).
- In production, a Model with no database is an error instead of an
  in-memory SQLite file.

### Fixed
- Rules on server function parameters (`Annotated[str, Field(max=40)]`) were
  ignored: a direct call could send any value, and forms didn't get the
  matching browser checks. They're enforced on every call now, with errors
  per field (422).
- `owner: User = Field(readonly=True)` made a plain column instead of a
  foreign key; options given with `Field(...)` now apply to the relation.
- `pyweb db seed --database URL` wrote to the app's own database; apps
  without migrations yet couldn't be seeded before `pyweb dev` created
  their tables.
- `pyweb dev` didn't reload pages larger than 1 KB after a change: they were
  gzipped before the reload script could be added.
- `Model.query()...live()` in a page made a static page: the compiler only
  recognised `live(db, sql)`.
- Schedules in UTC (including PyWeb's nightly cleanup) no longer need a
  time zone database, which Windows and Pyodide (the playground) don't
  ship; other zones explain that `pip install tzdata` provides one.
- The server answered a refused connection (503) or a rejected request and
  closed at once; unread request bytes then made the kernel reset the
  connection, which could destroy the response. It now closes politely
  (half-close, drain briefly).
- While developing, a Model's table made on first use inside a request that
  then failed was rolled back but still counted as made ("no such table"
  until a restart). Tables are now made when the app loads, and a lazily
  made one only counts once its transaction commits.
- Audit log entries written during a request that rolls back are kept
  (written when the transaction ends, committed or not), and a failing
  audit insert can no longer abort the request's transaction.
- `Site(..., rate_limit=False)` (used by `pyweb.testing.serve`) passed
  `False` to the server instead of turning rate limits off.

## [0.4.4]

Security and running in production. No app changes needed, and nobody
is signed out by the upgrade.

### Added
- **Signing keys per purpose, and rotation.** Sessions, live-update feeds
  and live query specs are signed with separate keys derived from
  `PYWEB_AUTH_SECRET`. `PYWEB_AUTH_SECRET_PREVIOUS` keeps tokens from an
  old secret working while you change it. Tokens from 0.4.3 and earlier
  still work.
- **Sessions:** each login starts a new session; sessions renew on visits
  and end 30 days after sign-in; `auth.revoke_user(user_id)` signs a user
  out on every device (in memory, or shared with `RedisSessionVersions`).
- **Typed server function arguments:** `list[...]`, `dict[...]`,
  `Optional`, `Literal`, dataclasses, Models and `Email` are checked
  against their type hints, and unknown arguments are refused (422).
- **`pyweb/config.py`:** every `PYWEB_*` setting in one place, validated
  at startup (a bad value stops the server with a clear message).
- **`PYWEB_REDIS_URL`:** shares rate limits (`RedisRateLimiter`) and live
  updates between server processes.
- **`/readyz`** checks that every database the app opened answers (503
  when one doesn't); `/healthz` stays a liveness check.
- **Graceful shutdown:** on SIGTERM and ASGI shutdown, live connections
  end and background jobs get up to 10 seconds to finish.
- **`pyweb deploy --target compose --domain example.com`** puts Caddy in
  front of the app with automatic HTTPS, keeps the app reachable only
  through it, and sets `PYWEB_TRUST_PROXY=1` and Secure cookies. The k8s
  target trusts its Ingress and uses `/readyz` for readiness. The
  deployment docs explain reverse proxies, with nginx and Caddy examples.
- **`pyweb check --production`** lists settings that would leave a gap
  (secret, HTTPS cookies, proxy, Redis with several workers, ...).
- **npm supply chain:** `pyweb.lock` records a SHA-256 per vendored file
  and `pyweb build` refuses files that changed.
- **CI:** dependency vulnerability audit (pip-audit), CodeQL code
  scanning, Dependabot, and signed build provenance for release files.
- **A much better playground** on the website: syntax highlighting with
  the error line marked (click the line number to jump to it), a Console
  that shows `print()` output, every server call with its arguments,
  status and time, and errors from the page; a draggable divider;
  phone/tablet/desktop preview widths and a reload button; highlighted
  JavaScript with its gzipped size; Reset, Download (`app.pyweb`, ready
  for `pyweb dev`) and autosave, so edits survive a reload; and a
  Code/Preview switch on phones.
- Docs: a threat model, key rotation, session lifetime, a production
  checklist, and the new settings.

### Security
- Requests the browser marks `Sec-Fetch-Site: cross-site` can't call
  server functions.
- Deeply nested JSON bodies crashed the request handler with a
  `RecursionError`; bodies deeper than 32 levels or with more than
  10,000 values are now refused.
- `pyweb serve` and `pyweb dev` drop clients that stall (30 s), answer
  503 past 256 simultaneous connections and 431 to headers over 16 KB.
- At most 20 open live connections per client address (429 beyond).
- Pages that take longer than 30 seconds to render answer 504 instead of
  holding a thread.
- New response headers: `Permissions-Policy`, `Cross-Origin-Opener-Policy`,
  `Cross-Origin-Resource-Policy`, and `Strict-Transport-Security` when
  `PYWEB_COOKIE_SECURE` is on. Both servers now send the same set.
- `RedisCache` no longer unpickles data unless `allow_pickle=True`
  (unpickling can run code written by anyone with Redis access).

### Fixed
- `@auth_required` server functions rejected sessions older than one
  hour, while pages accepted them for 7 days.

## [0.4.3]

A review release: bug fixes, security hardening and speed-ups. No API
removals; one new setting (`PYWEB_TRUST_PROXY`).

### Fixed
- Pages showed stale data when a module-level value (`ITEMS = []`) was
  changed by an `@server` function: the compiler copied its starting
  value into browser code, and the page re-rendered with it. Values
  that code changes now come from the server on each request.
- `db.transaction()`: a statement error that your code caught rolled
  back the transaction's earlier writes while later ones still
  committed. Errors inside a transaction now leave it to
  `transaction()` (all or nothing).
- `db.stream()` inside a transaction waited for a second connection
  (a 10 second hang with SQLite in memory); it now uses the
  transaction's.
- `pyweb serve` sent SVG, images, fonts, JSON and WebAssembly as
  `application/octet-stream` (browsers don't show an SVG sent that way).
- Your own files in `static/` (such as `app.css`) were cached by
  browsers for a year, so deploys didn't show up for returning
  visitors. They're now revalidated with an ETag (a `304` when
  unchanged); content-hashed files, `?v=` URLs and npm packages are
  still cached for a year.
- Client-side navigation could show a page prefetched before a server
  call changed its data.
- `async def` server functions ignored `rpc_timeout`.
- The ASGI adapter kept only the last of repeated request headers, so
  HTTP/2 clients sending each cookie separately lost all but one.
- `pyweb dts`: type aliases were never written, members on one line
  (`{ a: string; b?: number }`) produced invalid Python, and optional
  fields before required ones made the stub fail to import.
- Very long upload filenames are shortened (keeping the extension)
  instead of failing to save.

### Security
- RPC rate limits used the `X-Forwarded-For` header, which any client
  can set (so the limit was easy to get around), and put every visitor
  without a session cookie in one shared bucket. They now use the
  connection's address; behind a reverse proxy set `PYWEB_TRUST_PROXY=1`
  (or the number of proxies) to use the address your proxy saw.
- `auth.require_session` and `auth.login_response` only redirect to
  paths on your site (`//evil.example` becomes `/`); the new
  `auth.safe_next()` does the same for your own `?next=` handling.
- Magic-link tokens are signed separately from session cookies, so one
  can't be used as the other. Links issued before the upgrade stop
  working (they last 15 minutes).

### Performance
- HTML, JavaScript, CSS, JSON and SVG are gzipped for browsers that
  accept it (the shared runtime goes from 46 KB to 15 KB); static files
  are compressed once and kept.
- Memory no longer grows without bound in long-running servers: the
  rate limiter forgets idle callers, the realtime bus forgets channels
  quiet for an hour, the in-memory cache drops expired entries, and
  the job queue forgets jobs an hour after they finish.
- The job queue runs jobs on a pool of 8 threads instead of one thread
  per job, and `wait()` returns as soon as the job finishes.
- `pyweb dev` no longer re-scans virtualenvs in the project folder
  every 0.4 s, and reloads when `pyweb.lock` changes (after
  `pyweb add`).

## [0.4.2]

### Added
- VS Code extension: **PyWeb: Run App** (a run button on `.pyweb`
  files, Ctrl+F5), **New App** from any template, **Check App**, **Add
  npm Package**, **Install or Update PyWeb** and **Set Up AI Assistant
  (MCP)**, which adds the PyWeb MCP server to `.vscode/mcp.json`.
- VS Code extension: closing tags are added as you type, Emmet works in
  markup, new snippets (`layout`, `error`, `stream`, `livequery`, `npm`,
  `mount`), a status item that shows the PyWeb version in use, and an
  icon.

### Fixed
- VS Code extension: the language server failed with
  `spawn python ENOENT` when no Python interpreter was selected. The
  extension now looks for a Python with PyWeb (the selected
  interpreter, `pyweb`, `py`, `python3`, `python`), offers to install
  PyWeb when it's missing, and restarts when you change interpreter.

## [0.4.1]

### Added
- MCP server: `pyweb_routes` maps every page (route, path and query
  parameters with types and defaults, title and head tags, layouts,
  live-data variables), the layouts, error pages and server functions.
- MCP server: `pyweb_packages` adds, removes or lists npm packages for
  browser code, with each package's exported names and the `npm(...)`
  lines to bind them.
- MCP server: `add_ai_feature` and `make_data_live` prompts.
- MCP server: `pyweb_call` returns every chunk a streaming function
  yields; `pyweb_render` reports the title, head tags and the layouts that
  rendered; `pyweb_check` reports layouts, live variables, npm packages
  and streaming functions.
- The AI guide (`pyweb_guide`, `AGENTS.md`) has sections with working
  examples for multi-page apps, streaming and AI, live data and npm
  packages.
- A new README with screenshots, and docs on publishing the VS Code
  extension.

### Fixed
- Markup right after a `def` line that ends in a comment
  (`def Page(q: str = ""):  # ...`) failed to compile.

## [0.4.0]

Apps that are bigger, livelier and smarter: npm packages without
Node.js, multi-page apps with layouts and client-side navigation,
streaming for AI features, and page data that follows the database.

### Added
- **npm packages without Node.js.** `pyweb add chart.js/auto` downloads a
  package from the npm registry, checks its sha512 checksum, follows its
  imports from the browser entry point and copies only the files it
  needs (and its dependencies) into `static/vendor/`, pinning versions in
  `pyweb.lock`. `pyweb add` with no arguments reinstalls from the lock;
  `pyweb remove` takes a package out. Browser code binds exports with
  `Chart = npm("chart.js/auto")` or `npm("pkg", "Export")`. Calling a
  class constructs it and keyword arguments become an options object.
  Each page imports only the packages it uses, through an import map
  that the Content Security Policy allows by hash. CommonJS-only and
  Node.js-only packages are refused with an explanation, and an
  uninstalled package is a compile error that names the command to run.
- **`ref={el}`** sets a page variable to an element, in time for
  `on_mount`, for libraries that draw into the page.
- Changing an attribute of a package's object (`chart.value = 5`)
  re-renders whatever shows it.
- Web components (custom elements from npm) work in markup.
- `pyweb lsp`: hover shows an npm binding's installed version and
  TypeScript signature; completion offers installed packages in
  `npm("` and a module's exports after `name.`.
- New docs page: npm packages.
- **Layouts.** `@app.layout` wraps every page (or, with
  `@app.layout("/admin")`, pages under a prefix) in shared markup;
  `{children}` marks where the page goes. Layouts nest, have their own
  server code, state and handlers, and pages can opt out with
  `layout=None` or pick one with `layout="Name"`.
- **Client-side navigation.** Links between pages fetch the next page's
  HTML and swap it in below the layouts both pages share, so layout
  state (an open menu, a player) survives. Links are prefetched on
  hover or focus, back/forward restore the scroll position, and anything
  unusual falls back to a full page load. `navigate("/path")` from
  `pyweb.browser` does the same from browser code; `on_unmount` runs
  when a page is left; `App(client_nav=False)` turns it off.
- **Current links.** Links to the current page get
  `aria-current="page"` and links to its parent sections
  `aria-current="true"`, on the server and after each navigation.
- **Head tags.** `@app.page(description=..., image=..., noindex=...)` and
  `head(title=..., description=...)` in page code add description, Open
  Graph and Twitter tags; `App(base_url=...)` adds canonical URLs and
  absolute image links.
- **Error pages in `.pyweb`.** `@app.error(404)` / `@app.error(500)` pages
  take `path`, `status`, `message` or `request_id` and use root layouts.
- **Typed query parameters.** Page parameters that aren't in the route
  are read from the query string and converted by annotation (`int`,
  `float`, `bool`, `list[...]`); bad or missing values answer 400.
- New docs page: Layouts & navigation.
- **Streaming server functions.** A `@server` function that `yield`s
  sends each value as it's produced (NDJSON over the same RPC endpoint);
  browser code reads it with `async for`. `stream.cancel()` or leaving the
  loop aborts the request and closes the generator on the server, so an
  upstream AI call stops too. Errors raised part-way arrive as
  `RPCError`. Works with `async def` generators, under `pyweb serve`,
  `pyweb dev` and ASGI, and `TestClient.rpc` returns the streamed values.
- **`<Markdown text={...} />`** (from `pyweb`) renders Markdown to safe
  HTML on the server and updates it in the browser as the text changes,
  with the same rules in both places. Raw HTML is shown as text and
  links only accept http(s), mailto and relative URLs.
- **`ai-chat` template and example**: a streaming chat with Stop and
  Markdown replies that talks to Anthropic, any OpenAI-compatible server
  (OpenAI, Ollama, vLLM, ...) or a built-in demo model, chosen by
  environment variables. `pyweb new NAME --template ai-chat`.
- New docs page: Building AI apps.
- **Live data.** `rows = live(db, "select ...", params)` in a page or
  layout renders the rows and keeps them current: writes through
  `pyweb.db` (and `pyweb.models`) announce their table after commit, each
  distinct query re-runs once per change (bursts coalesced) and sends
  rows to every page showing it only when they changed. Pages follow a
  signed feed; `db.notify("table")` covers writes made elsewhere. With
  `RedisBus` it works across processes, and any process can take over a
  query from the page's signed description.
- New docs page: Live data.
- **`watch(lambda: value, handler)`** (from `pyweb.browser`) runs a
  handler whenever a value changes, for work outside markup such as
  redrawing a chart or saving a draft.
- `pyweb add` applies packages' `browser` field (browser versions of
  files, modules turned off), so packages such as ethers install.
- New examples: **dashboard** (live queries and a Chart.js chart from
  npm), **site** (layouts, navigation, query parameters, a 404 page) and
  **ai-chat**. The playground has the site and AI chat examples.
- New docs page: Recipe: wallets & web3 (wallet sign-in with ethers and
  eth-account).
- A layout's `{children}` can sit on a line of its own.

### Fixed
- Subscriptions and cleanups set up in `on_mount` now end when the page
  is left (they were never stopped).

### Changed
- The experimental `pyweb.live` module (in-memory live tables) is now
  `pyweb.livetable`; `pyweb.live` is the new live-query function.
- `pyweb.npm` (TypeScript declarations to dataclasses) is now
  `pyweb.dts`, and its command is `pyweb dts`. `pyweb build` no longer
  writes an esm.sh import map; packages come from `pyweb.lock`.
- The default Content Security Policy adds `worker-src 'self' blob:` so
  packages can start Web Workers.
- The starter stylesheet styles Markdown and chat bubbles.
- The browser runtime is about 14 KB gzipped (was about 11 KB), for
  client-side navigation and npm support.
- New website design: a colour system where blue means the browser and
  amber the server, self-hosted Inter / Bricolage Grotesque / JetBrains
  Mono, dark code windows, and a landing page that shows each line of an
  app with where it runs (worked out by the compiler) next to the real
  compiled output.
- The example apps and the `pyweb new` stylesheet use the new palette.

## [0.3.0]

Faster pages that feel like an app, apps that span several files, and
tools for editors, the browser and AI assistants.

### Added
- **Hydration.** Pages now adopt the server-rendered DOM instead of
  rebuilding it: nodes are kept, focus and text typed before the script
  loads survive, and typed values reach their variables. When the server
  HTML doesn't match, the page falls back to a client render and logs
  a warning. The page root carries `data-pw-mode="hydrated"` or
  `"rendered"`.
- **Live updates.** `publish(name, data)` in server code pushes to every
  page that called `subscribe(channel_feed, handler)`, over Server-Sent
  Events with a polling fallback. Feeds come from `channel(name)` while
  rendering: signed, expiring, and positioned so no message published
  after the render is missed. `pyweb dev`, `pyweb serve` and the ASGI
  adapter stream; `realtime.use_bus()` shares the bus (e.g. Redis)
  between processes. The chat example uses it instead of polling.
- **Multi-file apps.** `from widgets import Card, save, COLOR` imports
  components, `@server` functions and constants from `widgets.pyweb` (or
  `ui/cards.pyweb` as `ui.cards`). Each file keeps its own names in the
  generated JavaScript, imported files run as real modules on the
  server, `pyweb build` copies them into `dist/`, and errors name the
  file they're in. Pages stay in the app file; circular imports and
  duplicate server-function names are compile errors.
- **Editor support.** `pyweb lsp` is a language server (stdio, standard
  library only): compile errors and security warnings as you type,
  hover showing where each name runs and why, completion for components,
  props and tags, go to definition across files, and an outline. The new
  VS Code extension (`editors/vscode`) adds highlighting and snippets and
  starts it; releases attach a `.vsix`. Setup for Neovim and Helix is in
  the command-line docs.
- **Playground** on the website: edit an app and run it in the browser,
  with the real compiler, server rendering, `@server` functions,
  sessions and live updates running on Pyodide. Shows the compiled
  JavaScript and what runs where, reports errors with a fix, and shares
  code as links.
- **MCP tools** `pyweb_screenshot` (open a page in headless Chromium,
  run click/fill/press steps, get a screenshot, the page text, console
  errors and hydration status) and `pyweb_test` (run the app's pytest
  tests and report failures). New apps include a starter `test_app.py`.
- `python -m pyweb.bench --max-ssr-ms N --max-compile-ms N` fails when
  a benchmark exceeds its budget; CI runs it.

### Removed
- The old `pyweb.lsp` helper functions (`complete`, `DocumentState`,
  `complete_*`, `diagnostics_for_compile_error`, `hover(compiled, name)`,
  `boundary_lens`); they described an earlier API and no editor used them.

### Changed
- `import pyweb` no longer needs the `sqlite3` module (it's imported when
  a SQLite database is opened), and password hashing falls back to a
  pure-Python PBKDF2 on Pythons without OpenSSL. Both make PyWeb run on
  Pyodide.
- `/__pyweb/events` and `/__pyweb/poll` require a signed `?feed=`; the
  unauthenticated `?channel=` form is gone, so channels are no longer
  readable by anyone who guesses a name.
- Server rendering caches compiled template expressions; rendering a
  page is about twice as fast.
- Generated page modules pass element children as a function and wrap
  every child in a call (`$t`, `$dyn`), so nodes are created in document
  order.

## [0.2.0]

Tools for building PyWeb apps with AI assistants.

### Added
- `pyweb mcp`: a Model Context Protocol server (stdio, standard library
  only) for Claude Code, Cursor, Claude Desktop, VS Code and other MCP
  clients. Tools: `pyweb_guide`, `pyweb_new_app`, `pyweb_check` (errors
  with line numbers and fix hints), `pyweb_inspect`, `pyweb_compiled`,
  `pyweb_render` and `pyweb_call` (session cookies persist between calls);
  resources for the guide and templates; a `build_pyweb_app` prompt.
  Tested against the official MCP Python SDK.
- An AI-oriented guide to writing `.pyweb` apps, shipped in the package.
- `pyweb new --template blank|counter|todo|blog|auth|chat`; new projects
  include `AGENTS.md` and `CLAUDE.md` for coding agents.
- The docs site publishes `llms.txt` and `llms-full.txt`, and has a new
  "AI assistants & MCP" page.

## [0.1.0]

The first public release, published on PyPI as `pyweb-stack`. The
unpublished prototype was labelled "1.0" but only the simplest counter
pattern worked in a browser; this release rebuilds the compiler, runtime
and server around a design that works end to end, and is verified in a
real browser.

### Language and compiler
- `.pyweb` parser with a character scanner: tags and expressions may span
  lines, braces and strings nest inside `{...}`, `elif` chains, HTML
  comments, and exact line numbers (the Python half keeps the source's
  line count; this also fixes crashes on Python 3.10/3.11).
- Python → JavaScript translation of a documented subset with Python
  semantics (truthiness, `==` on containers, negative indexing, floor
  division/modulo, string/list/dict/set methods, Python exceptions).
  Unsupported code is a compile error with `file:line`. Verified
  differentially against CPython.
- State inference: signals (mutated by handlers or bound), computeds,
  constants; initial values from literals, the browser, or the server.
  Mutations (`append`, item assignment, `del`, …) are copy-on-write.
- Components with props, defaults and `children`; `on_mount` hook;
  page titles; `App(title=, stylesheets=, lang=)`.
- `@server` calls from handlers compile to awaited typed RPC; async
  propagates through handler calls.
- Only values browser code reads are serialised; values derived from
  server data are computed on the server. Secret-looking names read by
  browser code are rejected.

### Runtime
- New browser runtime: dependency-tracked signals, cached computeds,
  owned effects with disposal, batching; keyed lists and conditionals in
  marker-bounded regions; two-way binding for text, number, checkbox,
  radio and select; `javascript:` URL blocking; RPC client with typed
  errors.

### Server
- Pages render per request with real server values; `request`,
  `session`, `redirect`, `NotFound`; typed route parameters.
- `@server` functions are registered automatically in `dev`, `serve`,
  tests and ASGI.
- New ASGI adapter: `pyweb.asgi.create_app`.
- RPC: JSON-only and same-origin checks, rate limiting, timeouts that
  actually return on time, async functions, JSON conversion for
  dataclasses/datetimes/Decimals.
- Content-Security-Policy and security headers on HTML responses.

### Tooling
- `pyweb dev`: live reload and an in-browser compile-error overlay.
- `pyweb build` produces a self-contained `dist/` (source, manifest with
  gzip sizes, hashed assets, working Dockerfile); `pyweb serve` runs it.
- Token-aware JS minifier (the previous one altered string literals).
- `pyweb.testing.TestClient` and `pyweb.testing.serve` for tests.
- Clean `file:line: message` compile errors from the CLI.

### Data, auth, jobs
- `?` placeholders work on Postgres and MySQL; lazy connection pools.
- WebAuthn: challenge, origin and sign-count checks; DER signatures; uses
  `cryptography` when installed.
- Redis bus and queue work against real Redis.

### Fixed
- Migrations: the `pyweb_migrations` journal table can now be created on
  MySQL (`VARCHAR(255)` key instead of `TEXT`).
- `pyweb serve` crashed on startup (logger misconfiguration).
- Production builds referenced a hashed runtime the page modules never
  imported.
- Installed packages were missing the browser runtime file.
- `transaction()` did not roll back on Postgres/MySQL pools.
- `sqlite:///file.db` pointed at the filesystem root.
- `RedisBus.since()` duplicated and mis-ordered messages;
  `RedisQueue.drain(timeout=0)` blocked forever.
- Jobs raising `TypeError` ran twice.
- `Query.order_by()` ignored `-field` and did not validate a single field.
- The deploy Dockerfile ran a non-existent `serve --dir` flag.

### Removed
- Modules the framework no longer uses: `pyweb.components` (pure-Python
  element API), `pyweb.routing` (unused router), `pyweb.reactive`
  (server-side `Signal`/`Computed`/`Effect`/`live`), and
  `pyweb.compiler.placement` (superseded by the compiler's boundary checks).
- The `@browser` and `@shared` decorators, which had no effect.
- `pyweb.compiler.rpc.client_stub`/`server_handler` and unused AST classes.
- `docs/BUGLOG.md` (it described the prototype's internals) and the committed
  `website/dist/`; the Pages workflow builds the site.
- The regex-based codegen, the virtual test client that only recorded
  clicks, documentation "snippets" that did not use PyWeb, island and
  streaming-SSR string helpers without runtime support, and the no-op
  `--security-scan` flag.

## Pre-release prototype (unpublished)
- Initial prototype.
