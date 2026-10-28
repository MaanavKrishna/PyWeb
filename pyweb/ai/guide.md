# PyWeb guide for AI assistants

PyWeb (`pip install pyweb-stack`, `import pyweb`, command `pyweb`) builds a
full-stack web app from ONE `.pyweb` file: Python plus HTML-like markup.
Pages render on the server; event handlers compile to JavaScript; functions
marked `@server` run on the server and are called from the browser over RPC.

Follow these rules exactly. When unsure, run the `pyweb_check` tool (or
`pyweb check app.pyweb`) and fix what it reports.

## 1. Skeleton

```pyweb
from pyweb import App, server

app = App(title="My app", stylesheets=["/static/app.css"])


@server
def save_item(text: str) -> list:      # runs on the server; called over RPC
    ITEMS.append(text)
    return ITEMS


ITEMS = []


@app.page("/")
def Home():
    items = list(ITEMS)                # computed on the server per request
    draft = ""                         # browser state (bound below)

    def add():                         # event handler -> compiled to JavaScript
        items = save_item(draft)       # server call: awaited automatically
        draft = ""

    <main>
        <h1>Items ({len(items)})</h1>
        <form onsubmit={add}>
            <input bind={draft} placeholder="New item" />
            <button disabled={not draft.strip()}>Add</button>
        </form>
        <ul>
            for item in items:
                <li>{item}</li>
        </ul>
    </main>
```

Run: `pyweb dev app.pyweb` (http://localhost:8000, live reload).
Static files go in `static/` next to `app.pyweb`, served at `/static/...`.

## 2. What runs where (most important rules)

| Code | Runs |
|---|---|
| imports, classes, DB connections, module objects | server only |
| `NAME = <literal>` at module level | both (inlined into browser JS); server only if any code changes it (`.append`, `[k] =`, `global`) |
| `@server` functions | server; browser calls become RPC |
| undecorated module functions | server; compiled to JS only if browser code calls them |
| page function body (top to the markup) | server, every request |
| nested `def` inside a page (handlers) | browser (JavaScript) |
| `{expressions}` in markup | server (first render) and browser (updates) |

Consequences:
- Handlers must NOT use imports, DB handles, files, `os`, models, or other
  server-only names. Put that work in an `@server` function and call it.
- Markup expressions must not call `@server` functions. Load data into a
  page variable instead (`rows = load_rows()` at the top of the page).
- Never put secrets in page variables read by markup or handlers; the
  compiler rejects names like `token`, `secret`, `password`, `api_key`
  that would reach the browser (an empty `password = ""` bound to an input
  is fine).
- Everything the browser reads (markup, JS, page state) is public.
  Authorize inside `@server` functions with `session.require(...)`.

## 3. State

Plain local variables in a page are the state. No `useState`, no `nonlocal`.

- A variable assigned/mutated in a handler, or used with `bind={x}`, is a
  **signal**: reactive, updating only the DOM that reads it.
- A variable computed from signals and never assigned in a handler is
  **computed**: `total = price * quantity`.
- Everything else is a constant.
- Inside handlers, assigning a page variable updates the page:
  `count += 1`, `draft = ""`, `items.append(x)`, `items[i]["done"] = True`,
  `del items[i]`, `items = [x for x in items if ...]` all work.
- You cannot assign to a computed value or constant from a handler.
- Initial values that call functions, read the session, or depend on
  page-level logic are computed on the server; only values browser code
  reads are sent to the browser.

## 4. Markup

- A line starting with `<tag` is markup; tags may span lines; every
  non-void tag must be closed. Lowercase = HTML, Capitalized = component.
- `{expr}` inserts a value (None renders nothing). Text is escaped.
- Attributes: `class="x"` (literal), `href={url}` (expression),
  `disabled={flag}` (True/False toggles), `class={{"done": t["done"]}}`
  (dict of classes), `style={{"color": c}}` (dict of CSS).
- Events: `onclick={handler}`, `onclick={lambda: remove(item)}`, or
  `onclick={remove(item)}` (runs when clicked). `onsubmit` prevents the
  default submit. Any `on<event>` works: `oninput`, `onchange`, `onkeydown`.
- Binding: `bind={name}` on input/textarea/select; checkbox binds a bool;
  `type="number"` with a numeric initial value binds a number.
- Control flow lines inside markup: `for x in xs:`, `if c:`, `elif c:`,
  `else:` with markup bodies indented below.
- Whitespace between separate lines is dropped (like JSX); keep text that
  needs a space on one line.
- Literal braces: `{"{"}`.

## 5. Components

```pyweb
from pyweb import App, component

app = App()


@component
def Card(title, subtitle="", children=None):
    <section class="card">
        <h2>{title}</h2>
        if subtitle:
            <p>{subtitle}</p>
        {children}
    </section>


@app.page("/")
def Home():
    <Card title="Hello"><p>Body</p></Card>
```

Props are parameters (defaults = optional). Pass callbacks as props
(`on_delete={lambda: delete(i)}`) and use them as handlers inside
(`onclick={on_delete}`). A component's initial state must be computable in
the browser (pass server data as props).

Bigger apps can split into files: put components, `@server` functions and
constants in e.g. `widgets.pyweb` (or `ui/cards.pyweb`) next to `app.pyweb`
and `from widgets import Card, save`. Pages stay in `app.pyweb`; import
server functions without `as`; each file keeps its own constants.

## 6. Server functions, sessions, routing

```python
from pyweb import App, RPCError, NotFound, redirect, request, server, session

@server
def update(item_id: int, title: str) -> dict:     # annotations validate/coerce args
    user = session.require()                       # 401 if not signed in
    if not title.strip():
        raise RPCError("validation_error", "Title is required.")   # browser: except RPCError as e: str(e)
    ...

@app.page("/items/{item_id}")                      # typed route param; bad int -> 404
def Item(item_id: int):
    if not session.user():
        return redirect("/login")
    row = find(item_id)
    if row is None:
        raise NotFound()
    ...
```

- `session.login(user_id, **claims)`, `session.user()`, `session.logout()`,
  `session.require("admin")`. `pyweb.auth.revoke_user(user_id)` signs a user
  out on every device (e.g. after a password change).
- Give every `@server` parameter a type hint (`str`, `int`, `list[int]`,
  `date`, `Decimal`, a dataclass or Model, `Literal[...]`): arguments are
  checked against them, and wrong types or unknown arguments get a 422
  before your code runs. Still check permissions and business rules
  yourself; limit expensive functions with `@server(rate="5/minute")`.
- `pyweb.auth.hash_password` / `verify_password` for passwords.
- Data: prefer Models. `app = App(database="sqlite:///app.db")`, then
  `class Post(Model): title: str = Field(max=120); author: User; tags: list[Tag] = []`
  (`author: User` is a foreign key, `list[Tag]` many-to-many, `| None`
  optional). `Post.create(...)`, `Post.get(id)`, `Post.get_or_404(id)`,
  `Post.where(Post.views > 10, author=user).order("-id").limit(20)`,
  `.include("author", "tags")` before reading relations (reading one that
  wasn't included is an error while developing), `.page(size=20, after=cursor)`
  for pagination, `.count()`, `.values(...)`, `.update(...)`, `.delete()`.
  Put validation in `Field(min=, max=, pattern=, choices=)` and
  `@validates("field")`; `ValidationError` reaches the browser as field errors.
  Each `@server` call is one transaction. Schema changes: `pyweb db diff`
  then `pyweb db upgrade` (`--contract` later for removals).
- Forms: `@server def save_post(post: Post): post.save(); return post`, then
  `<Form action={save_post} redirect="/posts/{id}"><Input name="title" />`
  `<Textarea name="body" /><Select name="tags" /><Checkbox name="draft" />`
  `<Submit>Save</Submit></Form>` (import the tags from `pyweb`). Rules come
  from the Model; don't add your own JS validation. `values={post}` edits a
  row. Raise `ValidationError({"field": "message"})` for your own checks.
  Uploads: `photo: str | None = File(types=["image/*"])` + `<FileInput name="photo" />`.
- Accounts: `auth = app.use_auth()` (after `App(database=...)`) gives
  `/signup`, `/login`, `/logout`, `/reset`, `/account` and `/admin`; don't
  write your own login pages, password hashing or reset emails. Guard with
  `@app.page("/x", login=True, roles=["admin"], fresh=600)`, the same on
  `@app.layout` and `@server(login=True)`. In code: `user = auth.user()`
  (a `User` row or None), `auth.require("admin")`, `auth.set_roles(user, [...])`.
  Scope rows per user with `Note.policy(read=lambda user: Note.owner_id == user.id,
  write=lambda user, note: note.owner_id == user.id)` instead of checking
  ownership by hand in every function. OAuth: `use_auth(providers=["github"])`
  plus `PYWEB_OAUTH_GITHUB_ID/SECRET`. Production needs `PYWEB_ORIGIN` and
  `PYWEB_MAIL_URL`; while developing, emailed links are printed in the terminal.
- Background work: `@app.job(retries=5)` then `fn.enqueue(id)` from a
  server function (queued only if its writes commit; pass ids, not objects;
  make jobs safe to run twice). Schedules: `@app.cron("0 3 * * *")`,
  `@app.every(minutes=5)`. Don't start threads or `time.sleep` loops in
  server code, and don't send email inline: `pyweb.mail.send` already goes
  through the job queue.
- Raw SQL when needed: `from pyweb.db import connect; db = connect(url)`;
  `db.execute("select ... where id = ?", (x,)).dicts()`; always use `?`
  parameters; `with db.transaction(): ...`.
- Run code after load with a handler named `on_mount`; stop timers in
  `on_unmount` (runs when the user navigates away). React to a value
  changing with `watch(lambda: value, handler)` in `on_mount`
  (`from pyweb.browser import watch`).
- Live updates: in a server function `publish("room:1", data)`; in the
  page `feed = channel("room:1")` (runs on the server); in `on_mount`
  `subscribe(feed, handler)`, where `handler(message)` assigns page
  variables. Import all three from `pyweb`. Never poll with
  `setInterval` when `publish` fits. For lists from the database prefer
  `rows = live(db, sql)` or `Post.where(...).live()` in the page: it
  updates by itself, sending only changed rows (keep an `id` column).
- Presence: `room = presence("doc:1")` in the page, then in `on_mount`
  `me = join(room, {"name": name}, on_members)`; `me.cast(data)` reaches
  the others' `on_cast(data, sender)`. Trust `member["user"]`, not names.
- Serve with `pyweb serve dist --workers 4` (PyWeb's own server: WebSockets,
  keep-alive, graceful SIGTERM); set `PYWEB_REDIS_URL` with several workers.
- Deploying: run `pyweb deploy <docker|compose|k8s|fly|render|railway>`
  and follow the plan it prints; don't hand-write Dockerfiles or manifests.
  Keep `App(database="sqlite:///app.db")` in code: `DATABASE_URL` replaces
  it in production. Commit `migrations/` (`pyweb db diff`).
- Observability is built in: use `logging.getLogger(__name__)` (lines get
  the request id, route and user; secrets are redacted), never `print`.
  `with span("charge card"):` marks slow parts; `@app.on_error` forwards
  unhandled errors (e.g. to Sentry). The dev toolbar (`pyweb dev`) shows
  each request's SQL: a "N+1?" warning means add `include(...)`.
  `/metrics` needs `PYWEB_METRICS_TOKEN` in production.
- **HTTP routes** (JSON APIs, CSV downloads, anything that isn't a page):
  `@app.route("/api/items/{id:int}", methods=["GET", "POST"], login=True)` on a
  plain function; it gets path/query parameters by name, `body` (JSON) and
  `request`, and returns a dict/list (JSON), a str, bytes, None (204) or
  `pyweb.routes.json(...)/csv(rows, filename=)/redirect(...)`. Use server
  functions for the app's own pages; routes for other clients.
- `@app.middleware` `def mw(request, call_next): ...` wraps every request.
- Translations: `App(locales=["en", "de"])`, `from pyweb import _, ngettext`;
  `{_("Welcome")}` and `{ngettext("{n} item", "{n} items", count)}` in markup;
  `pyweb i18n extract` / `init de` / `check`; `set_locale(lang)` in a server function.
- `<ErrorBoundary fallback="...">children</ErrorBoundary>` (from `pyweb`) keeps a
  failing part from breaking the page (server render and browser handlers).
- Tests: `client.get("/").find("li.done").text`, `client.render("Badge", label="x")`,
  `client.a11y("/") == []`, `assert_snapshot(text, "name")`.
- Read-mostly public pages: `@app.page("/pricing", cache=300)` keeps the HTML for
  visitors who aren't signed in (dropped when a table it read changes).

## 7. Python that compiles to the browser

Supported in handlers/markup: literals, f-strings (with format specs),
arithmetic with Python semantics, comparisons, `in`, `and/or/not` with
Python truthiness, comprehensions, lambdas, slicing/negative indexes,
`if/for/while/try/except/raise/return/del`, builtins (`len str int float
bool abs min max sum round range sorted reversed enumerate zip list dict
set tuple any all isinstance print`), common str/list/dict/set methods.
JS globals are available directly: `window`, `document`, `localStorage`,
`console`, `setTimeout`, `setInterval`, `fetch`, `Math`, `JSON`, `Date`.
`import math` at the top of the file works in browser code too, and so do
plain functions imported from another `.pyweb` file.

Not supported in browser code: classes, imports, `with`, generators,
walrus, `*args/**kwargs` parameters, slice assignment, keyword arguments to
browser globals, server-only names. Move such code into `@server` functions.

## 8. Errors and fixes

| Error text contains | Fix |
|---|---|
| `only exists on the server` | Move that logic into an `@server` function and call it from the handler. |
| `is not defined in browser code` | Define it at module level (literal or helper function), pass it in, or use an `@server` function. |
| `markup expressions must be synchronous` | Assign the server call's result to a page variable or call it in a handler. |
| `cannot assign to ... derived/read-only` | Assign to a variable the handler owns (make it state), not a computed/constant. |
| `server secret ... would be sent to the browser` | Keep the value inside `@server` functions; don't read it in markup/handlers. |
| `bind={x} must name a local variable` | Declare `x = ""` (or a number/bool) in the page before the markup. |
| `unknown component <X>` | Define `def X(...)` with markup (capitalized) in this file, or import it: `from widgets import X`. |
| `mismatched </tag>` / `is never closed` | Close every tag; void tags (`input`, `img`, `br`) need no close (`<input ... />`). |
| `... is not supported in browser code` | Rewrite with supported constructs or move it to `@server`. |
| `npm package 'x' isn't installed` | Run `pyweb add x` in the app folder (the folder with `pyweb.lock`). |
| `uses X from npm(...), which only exists in the browser` | Use the package in a handler or `on_mount` and store the result in a page variable that markup shows. |
| `layout ... has no {children}` | Put `{children}` exactly once in the layout's markup where pages go. |
| `isn't a valid int` / `missing ?name=` (400) | Give the query parameter a default, or link with a valid value. |
| `pyweb add`: `is CommonJS` / `imports the Node.js module` | Pick an ES-module browser package (e.g. `lodash-es`), or do the work in an `@server` function. |

## 9. Multi-page apps

```pyweb
from pyweb import App, NotFound, head

app = App(title="Shop", base_url="https://shop.example")   # base_url: canonical URLs


@app.layout                                  # wraps every page; @app.layout("/admin") for a prefix
def Shell(children):
    cart = 0                                 # layout state survives page changes

    def add():
        cart += 1

    <nav><a href="/">Home</a><a href="/products">Products</a></nav>
    <button onclick={add}>Cart {cart}</button>
    {children}


@app.page("/products", title="Products", description="Everything we sell")
def Products(q: str = "", page: int = 1):    # not in the route -> query string, typed; bad -> 400
    <h1>Results for {q}, page {page}</h1>


@app.page("/products/{pid}")
def Product(pid: int):
    if pid != 1:
        raise NotFound()
    head(title="Lamp - Shop", description="A lamp")        # per-page head tags, on the server
    <h1>Lamp</h1>


@app.error(404)
def Missing(path):
    <h1>Nothing at {path}</h1>
```

- Put `{children}` exactly once in a layout. `@app.page(..., layout=None)`
  opts out; `layout="Admin"` picks one.
- Plain `<a href>` links load without a full reload; layouts keep their
  state; links to the current page get `aria-current="page"`
  automatically (style `a[aria-current]`). Don't add active-link logic.
- From handlers: `navigate("/path")` (`from pyweb.browser import navigate`).
- A layout that shows per-page server data (like `request.path`) is
  re-rendered on every navigation; keep layout data stable.

## 10. Streaming and AI features

```pyweb
import os

from pyweb import App, Markdown, RPCError, server

app = App()


@server
def reply(question: str):                    # a generator: each yield reaches the browser at once
    if not question.strip():
        raise RPCError("validation_error", "Ask something")
    for word in ("**Hello**", " from", " the", " server"):
        yield word                           # call your model API here (key from os.environ)


@app.page("/")
def Chat():
    question = ""
    answer = ""
    busy = False
    stream = None

    async def ask():
        busy = True
        answer = ""
        stream = reply(question)
        try:
            async for piece in stream:
                answer += piece
        except RPCError as e:
            answer = str(e)
        busy = False

    def stop():
        if stream:
            stream.cancel()                  # aborts the request and closes the generator

    def on_unmount():
        stop()

    <input bind={question} />
    <button onclick={ask} disabled={busy}>Ask</button>
    <button onclick={stop}>Stop</button>
    <Markdown text={answer} />
```

- Handlers that use `async for` are `async def`. Calling a streaming
  function returns a stream; `break` or `.cancel()` stops it.
- `<Markdown text={...} />` is safe on model output (no raw HTML, no
  `javascript:` links) and renders as text streams in.
- API keys: only in `@server` code via `os.environ`. Validate and trim
  the conversation the browser sends (roles, length).
- Full example: `pyweb new NAME --template ai-chat` (Anthropic,
  OpenAI-compatible servers such as Ollama, or a built-in demo model).
- `TestClient.rpc("reply", question="hi")` returns the list of yielded values.

## 11. Live data

```pyweb
from pyweb import App, Field, Model, server

app = App(database="sqlite:///app.db")


class Todo(Model):
    title: str = Field(max=200)


@server
def add(title: str) -> None:
    Todo.create(title=title)            # announces "todos" after the commit


@app.page("/")
def Todos():
    todos = Todo.query().order("-id").limit(50).live()

    <ul>
        for t in todos:
            <li>{t["title"]}</li>
    </ul>
```

- Every open page showing `todos` updates when the table is written
  through Models or `pyweb.db`; only changed rows are sent. Don't add
  publish / subscribe or polling for this. Raw SQL works the same way:
  `rows = live(db, "select id, title from todos ...")`.
- Live variables are reactive in the browser; use `watch(lambda: todos, fn)`
  for side effects (redrawing a chart).
- Filter per user with `.where(owner=user)` or a row policy, keep
  queries small (`.limit()`), and call `db.notify("table")` after writes
  made by another program.
- Several processes: `realtime.use_bus(RedisBus(url))`.

## 12. npm packages

- Add with `pyweb add chart.js/auto` (MCP: `pyweb_packages`) in the app
  folder; it writes `pyweb.lock` and `static/vendor/` (commit both; no
  Node.js needed). Remove with `pyweb remove NAME`.
- Bind at module level with literal strings: `Chart = npm("chart.js/auto")`
  (default export), `Gauge = npm("pkg", "Gauge")` (named export),
  `lib = npm("pkg", "*")` (whole module). `from pyweb import npm`.
- Calling a class constructs it (`new`); keyword arguments become one
  options object: `confetti(particleCount=80)`.
- npm names work only in browser code (handlers, `on_mount`, lambdas),
  never directly in markup: store results in page variables.
- Give libraries an element with `ref={el}` (declare `el = None`; it is
  set before `on_mount`).

## 13. UI kit and motion

Build interfaces from `pyweb.ui` instead of hand-written CSS and JS. Its
components render on the server, are keyboard-accessible, and follow the
app's theme (the sign-in and admin pages too):

```python
from pyweb.ui import (Badge, Button, Card, CardContent, CardHeader, CardTitle, Dialog, DialogContent,
                      DialogHeader, DialogTitle, DialogTrigger, Theme, toast)

app = App(title="Tracker", database="sqlite:///app.db", theme=Theme.preset("violet"))

    <Card>
        <CardHeader><CardTitle>Issues</CardTitle></CardHeader>
        <CardContent>
            <Dialog>
                <DialogTrigger id="new">New issue</DialogTrigger>
                <DialogContent>
                    <DialogHeader><DialogTitle>New issue</DialogTitle></DialogHeader>
                    <Form action={add_issue}>...</Form>
                </DialogContent>
            </Dialog>
            <Button variant="outline" size="sm" href="/orgs">Team</Button>
        </CardContent>
    </Card>
```

- List components and their `variant`/`size` values with the `pyweb_ui`
  MCP tool (or read `pyweb://kit/<Name>`). There are buttons, cards,
  badges, alerts, tables and data tables, charts, tabs, accordions,
  dialogs and sheets, dropdown and context menus, popovers, tooltips,
  selects, comboboxes, a command palette, date pickers, sliders, switches,
  checkboxes, toggle groups, a sidebar layout, pagination, carousels.
- Every component takes `class_=`, `id=`, `aria-*` and event attributes.
- `toast("Saved", "details", tone="success")` from a server function shows
  a toast in the browser.
- Theme: `Theme(accent="#0f766e", radius=8, font="inter", density="compact")`
  or `Theme.preset("zinc" | "slate" | "rose" | "ocean" | "violet" | "emerald" | "amber" | "indigo")`.
- Your own variants: `ui.extend("Button", variant={"brand": {...css}})`;
  your own components: `variants()` / `style()` / `cx()` from `pyweb.ui`.
- Motion on any element: `animate="fade-up"` (presets: fade, fade-up/down/left/right,
  slide-*, scale, pop, blur-in, flip, collapse, shake, pulse), or
  `initial={{"opacity": 0, "y": 8}} animate={{"opacity": 1, "y": 0}} exit={{"opacity": 0}}`,
  `layout` (smooth reordering, e.g. on rows of a live list), `while_hover=`,
  `while_tap=`, `drag`. `App(motion=Motion(feel="snappy" | "gentle" | "bouncy" | "none"))` (`from pyweb.motion import Motion`)
  sets the feel everywhere; reduced-motion settings are honoured.

## 14. Teams (organizations)

For products where people work in teams, use `app.use_auth(orgs=True)`:
`/orgs` lists a user's organizations, `/orgs/<slug>` invites members and
sets roles (owner, admin, member, viewer).

```python
auth = app.use_auth(orgs=True)
auth.plans({"free": {"seats": 3, "exports": False}, "pro": {"seats": 50, "exports": True}})


class Issue(Model, tenant=True):          # rows belong to the current organization
    title: str = Field(min=3, max=120)


@server(org_role=["owner", "admin"], feature="exports")
def export_issues() -> list:
    return [i.to_dict() for i in Issue.query()]
```

- Every query on a `tenant=True` Model (including `get_or_404`, relations,
  `.live()` lists, bulk updates) only sees the current organization's
  rows, and new rows get it automatically. Don't add `org_id` filters or
  ownership policies for that; don't use `.unscoped()` outside
  `with system():`.
- `auth.org()` is the current organization (None until the user has one:
  create it with `auth.create_org(name)` then `auth.switch_org(org)`),
  `auth.role()` the user's role in it.
- Guards: `org_role=` and `feature=` work on `@server`, `@app.page` and
  `@app.layout`. Plans are True/False features and numeric limits
  (`seats` is enforced on invite); your billing code changes `org.plan`.
- Jobs queued in a request run for the same organization.
- API tokens: users make them at `/account/tokens`; scripts call server
  functions with `Authorization: Bearer pwk_...`. `pyweb openapi` describes them.
- Tests: `client.login("ann@example.com", org="Acme", org_role="member")`.

## 15. Installable apps, desktop apps, webhooks, security

- **Installable (PWA):** `App(pwa=True)` (or `pwa=PWA(name=, icon="static/icon.png",
  theme_color=, cache_pages=True)` from `pyweb.pwa`): manifest, icons, a
  service worker and an offline page. Push: `pip install "pyweb-stack[push]"`,
  a button with `data-pw-push="subscribe"`, then `push.send(user, title, body, url)`.
- **Desktop:** `App(desktop=Window(width=1100, height=760))` (from
  `pyweb.desktop`), run with `pyweb desktop app.pyweb`, package with
  `pyweb build --desktop app.pyweb --package`. Native dialogs
  (`desktop.open_file`, `save_file`, `choose_folder`, `notify`) are called
  from `@server` functions; show their buttons only when `platform.desktop`
  (`from pyweb import platform`) is true.
- **Webhooks in:** `@app.webhook("/hooks/stripe", secret=webhooks.env_secret("STRIPE_SECRET"),
  scheme="stripe")` (or `"github"`, `"standard"`) on `def on_stripe(event):`
  (`event.type`, `event.data`). Signatures, replays and retries are handled.
  **Out:** `webhooks.send(url, "invoice.paid", data, secret=...)`, or
  `webhooks.publish(event, data)` to every endpoint of the current organization.
- **Rate limits:** `@server(rate="5/minute")` on anything expensive or
  abusable (`per="user"` default, `"ip"`, `"org"`, `"all"`).
- **Fetching URLs users give you:** `from pyweb.http import fetch` (refuses
  internal addresses), never `urllib`/`requests` directly.
- **Sensitive fields:** `Field(encrypted=True)` (needs `PYWEB_DATA_KEY` in
  production), `searchable=True` to look rows up by them; `private=True`
  for values that must never reach a browser.
- Don't put API keys in the source: `pyweb check` fails on them. Read
  them with `os.environ` in server code.
- Before shipping: `pyweb check --production` (MCP: `pyweb_security_check`
  with `production=true`).

## 16. Workflow for agents

1. Start from a template: `pyweb new NAME --template saas` (or the
   `pyweb_new_app` MCP tool). Templates: blank, counter, todo, blog, saas
   (teams, accounts, tenant Models, forms, jobs, admin: the best start for a
   product), auth, chat, ai-chat, desktop (a notes app in its own window).
2. Edit `app.pyweb`. After every edit run `pyweb check app.pyweb`
   (MCP: `pyweb_check`) and fix errors by line number. Look things up with
   `pyweb_docs`; `pyweb_explain` says where a line's names run and what can
   go there; `pyweb_ui` lists the kit's components.
3. Need a JavaScript library? `pyweb add NAME` (MCP: `pyweb_packages`).
   Use `pyweb inspect` (MCP: `pyweb_inspect`) to confirm what runs in the
   browser vs server and what is sent to the browser.
4. Verify behaviour: list pages, layouts and parameters (`pyweb_routes`),
   render pages (`pyweb_render`) and call server functions (`pyweb_call`),
   as a signed-in member with `as_user`/`org`. See the page and try
   interactions in a real browser with `pyweb_screenshot` (steps: click,
   fill, press, ...), and check it in light, dark and at phone width with
   an accessibility audit using `pyweb_review`. If the user runs
   `pyweb dev`, `pyweb_dev` and `pyweb_logs` show its errors and logs.
5. Data: after changing a Model, `pyweb db diff` then `pyweb db upgrade`
   (MCP: `pyweb_migrations`). Look at tables and rows with
   `pyweb_db_schema` / `pyweb_db_query` (read-only), at jobs with
   `pyweb_jobs`, and at each request's SQL with `pyweb_requests`.
6. Test: new apps include `test_app.py` (`pyweb.testing.TestClient`); add
   tests for what you change and run `pytest` (MCP: `pyweb_test`).
   `client.login("ann@example.com", roles=["admin"])` signs a test in.
7. Ship: `pyweb_security_check` (with `production=true`), then
   `pyweb deploy compose` (or docker, k8s, fly, render, railway) writes
   everything and explains it (`pyweb_deploy_plan` shows the plan without
   writing); never hand-write Dockerfiles.

Full docs: https://maanavkrishna.github.io/PyWeb/
