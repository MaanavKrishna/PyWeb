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
  a dataclass or Model, `Literal[...]`): arguments are checked against
  them, and wrong types or unknown arguments get a 422 before your code
  runs. Still check permissions and business rules yourself.
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

## 7. Python that compiles to the browser

Supported in handlers/markup: literals, f-strings (with format specs),
arithmetic with Python semantics, comparisons, `in`, `and/or/not` with
Python truthiness, comprehensions, lambdas, slicing/negative indexes,
`if/for/while/try/except/raise/return/del`, builtins (`len str int float
bool abs min max sum round range sorted reversed enumerate zip list dict
set tuple any all isinstance print`), common str/list/dict/set methods.
JS globals are available directly: `window`, `document`, `localStorage`,
`console`, `setTimeout`, `setInterval`, `fetch`, `Math`, `JSON`, `Date`.

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
from pyweb import App, live, server
from pyweb.db import connect

app = App()
db = connect("sqlite:///app.db")


@server
def add(title: str) -> None:
    db.execute("insert into todos (title) values (?)", (title,))   # announces "todos" after commit


@app.page("/")
def Todos():
    todos = live(db, "select id, title from todos order by id desc limit 50")

    <ul>
        for t in todos:
            <li>{t["title"]}</li>
    </ul>
```

- Every open page showing `todos` updates when the table is written
  through `pyweb.db` (including `pyweb.models`). Don't add publish /
  subscribe or polling for this.
- Live variables are reactive in the browser; use `watch(lambda: todos, fn)`
  for side effects (redrawing a chart).
- Filter per user in SQL (`where owner = ?`), keep queries small
  (`LIMIT`), and call `db.notify("table")` after writes made elsewhere.
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

## 13. Workflow for agents

1. Start from a template: `pyweb new NAME --template todo` (or the
   `pyweb_new_app` MCP tool). Templates: blank, counter, todo, blog, auth, chat, ai-chat.
2. Edit `app.pyweb`. After every edit run `pyweb check app.pyweb`
   (MCP: `pyweb_check`) and fix errors by line number.
3. Need a JavaScript library? `pyweb add NAME` (MCP: `pyweb_packages`).
   Use `pyweb inspect` (MCP: `pyweb_inspect`) to confirm what runs in the
   browser vs server and what is sent to the browser.
4. Verify behaviour: list pages, layouts and parameters (`pyweb_routes`),
   render pages (`pyweb_render`) and call server functions (`pyweb_call`). See the page and try interactions in a real
   browser with `pyweb_screenshot` (steps: click, fill, press, ...).
5. Test: new apps include `test_app.py` (`pyweb.testing.TestClient`); add
   tests for what you change and run `pytest` (MCP: `pyweb_test`).
6. Ship: `pyweb build app.pyweb --out dist --production` then
   `pyweb serve dist` (set `PYWEB_AUTH_SECRET` in production).

Full docs: https://maanavkrishna.github.io/PyWeb/
