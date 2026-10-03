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
| `NAME = <literal>` at module level | both (inlined into browser JS) |
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
  `session.require("admin")`.
- `pyweb.auth.hash_password` / `verify_password` for passwords.
- Database: `from pyweb.db import connect; db = connect("sqlite:///app.db")`;
  `db.execute("select ... where id = ?", (x,)).dicts()`; always use `?`
  parameters; `with db.transaction(): ...`.
- Query parameters: page params not in the route come from the query
  string, typed by annotation: `def Search(q: str = "", page: int = 1)`.
  Missing required / bad values -> 400.
- Layouts: `@app.layout` (or `@app.layout("/admin")`) on a function with
  markup containing `{children}` once wraps every page under the prefix;
  `@app.page(..., layout=None)` opts out. Links between pages load without
  a full reload and keep the layout's state; mark nothing yourself: links
  to the current page get `aria-current="page"` automatically.
- In handlers, navigate with `navigate("/path")` (`from pyweb.browser import navigate`).
- Head tags: `@app.page("/", title=..., description=..., image=...)`, or
  `head(title=..., description=...)` in the page body (server). `App(base_url=...)`
  adds canonical URLs.
- Error pages: `@app.error(404)` on a page function taking `path` (and/or
  `status`, `message`, `request_id`).
- Run code after load with a handler named `on_mount`; stop timers in
  `on_unmount` (runs when the user navigates away). React to a value
  changing with `watch(lambda: value, handler)` in `on_mount`
  (`from pyweb.browser import watch`).
- Live data: `rows = live(db, "select ... where x = ?", (x,))` in a page
  (`from pyweb import live`) renders the rows and keeps them current in
  every open page when the tables are written through `pyweb.db`. No
  publish/subscribe needed. Use `db.notify("table")` after writes made
  outside `pyweb.db`. Keep live queries small (`LIMIT`).
- Streaming (AI replies, progress): a `@server` function that `yield`s;
  in an `async def` handler `stream = fn(...)` then
  `async for piece in stream: ...`; `stream.cancel()` stops it (closes the
  generator on the server). Show model output with `<Markdown text={reply} />`
  (`from pyweb import Markdown`): safe, renders as it streams. Template:
  `pyweb new NAME --template ai-chat`. Keep API keys in server code
  (`os.environ`), never in page variables.
- Live updates: in a server function `publish("room:1", data)`; in the
  page `feed = channel("room:1")` (runs on the server); in `on_mount`
  `subscribe(feed, handler)`, where `handler(message)` assigns page
  variables. Import all three from `pyweb`. Never poll with
  `setInterval` when `publish` fits.

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

npm packages (no Node.js needed): run `pyweb add chart.js/auto` in the app
folder (writes `pyweb.lock` and `static/vendor/`; commit both), then bind at
module level with literal strings: `Chart = npm("chart.js/auto")`,
`Gauge = npm("pkg", "Gauge")` (named export), `lib = npm("pkg", "*")` (whole
module). Calling a class constructs it (`new`); keyword arguments become one
options object. npm names work only in browser code (handlers, `on_mount`,
lambdas), never directly in markup. Give libraries an element with
`ref={el}` (declare `el = None`; it is set before `on_mount`).

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

## 9. Workflow for agents

1. Start from a template: `pyweb new NAME --template todo` (or the
   `pyweb_new_app` MCP tool). Templates: blank, counter, todo, blog, auth, chat, ai-chat.
2. Edit `app.pyweb`. After every edit run `pyweb check app.pyweb`
   (MCP: `pyweb_check`) and fix errors by line number.
3. Use `pyweb inspect` (MCP: `pyweb_inspect`) to confirm what runs in the
   browser vs server and what is sent to the browser.
4. Verify behaviour: render pages (`pyweb_render`) and call server
   functions (`pyweb_call`). See the page and try interactions in a real
   browser with `pyweb_screenshot` (steps: click, fill, press, ...).
5. Test: new apps include `test_app.py` (`pyweb.testing.TestClient`); add
   tests for what you change and run `pytest` (MCP: `pyweb_test`).
6. Ship: `pyweb build app.pyweb --out dist --production` then
   `pyweb serve dist` (set `PYWEB_AUTH_SECRET` in production).

Full docs: https://maanavkrishna.github.io/PyWeb/
