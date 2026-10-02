# The `.pyweb` language

A `.pyweb` file is a Python module in which page and component functions
may contain **markup statements**. Everything else is ordinary Python
and is parsed by CPython's own parser, so line numbers, scoping and
error messages are exact.

## File structure

```pyweb
import os                         # imports: server-side only

from pyweb import App, component, server

app = App(title="My app", stylesheets=["/static/app.css"], lang="en")

PAGE_SIZE = 20                    # literal module constants: usable everywhere


def slugify(text):                # plain helper: compiled to JS if browser code calls it
    return text.lower().replace(" ", "-")


@server                           # runs on the server, callable from the browser
def search(q: str) -> list:
    return []


@component                        # markup + props
def Badge(label, children=None):
    <span class="badge">{label}: {children}</span>


@app.page("/")                    # a page: route + markup
def Home():
    q = ""
    <h1>Hello</h1>
```

| Module-level item | Where it runs |
|---|---|
| `import ...` / `from ... import ...` | Server only. Names imported this way are never available in browser code (except `pyweb.browser`, below). |
| `NAME = <literal>` (numbers, strings, lists, dicts, …) | Both. Inlined into browser code when referenced. |
| `NAME = <anything else>` (connections, objects) | Server only. |
| `@server` function | Server. Calls from browser code become RPC. |
| Undecorated function | Server; also compiled to JavaScript on demand when browser code calls it. Compile error if it can't run in a browser. |
| `@component` / capitalised function with markup | Rendered on the server and in the browser. |
| `@app.page(route)` function | Rendered per request on the server, interactive in the browser. |
| `class` | Server only. |

## Markup statements

A line starting with `<tag` is markup. Markup can span several lines and
nest by its own tags; indentation inside markup is cosmetic.

```pyweb
from pyweb import App

app = App()


@app.page("/")
def Home():
    name = "Ada"
    items = ["a", "b"]
    show = True

    <main class="page">
        <h1>Hello, {name}!</h1>
        <img
            src="/static/logo.png"
            alt="Logo" />
        <p>{len(items)} items</p>
        <!-- comments are dropped -->
    </main>
```

### Text and expressions

- Text inside a tag is HTML-escaped.
- `{expr}` inserts the value of any Python expression; it is
  re-evaluated when the state it reads changes.
- Values render like `str(value)`, except `None` renders nothing.
- Whitespace within a line is kept (collapsed to single spaces); line
  breaks between lines are not, as in JSX. Put text that needs a
  separating space on the same line.
- To write a literal brace, use an expression: `{"{"}`.

### Attributes

| Form | Meaning |
|---|---|
| `class="card"` | Literal string. No interpolation inside quotes. |
| `class={expr}` | Python expression; updates reactively. |
| `disabled` | Boolean attribute (present). |
| `disabled={flag}` | `True` → present, `False`/`None` → absent. |
| `class={{"done": t["done"], "row": True}}` | A dict: keys with truthy values become classes. |
| `style={{"color": color, "font_size": "14px"}}` | A dict of CSS properties; `snake_case`/`camelCase` become `kebab-case`. |
| `href={url}`, `src`, `action` | URLs starting with `javascript:` are replaced by `#`. |
| `onclick={handler}` | Event handler (see below). Any `on<event>` works: `oninput`, `onchange`, `onsubmit`, `onkeydown`, … |
| `bind={name}` | Two-way binding to a page variable. |

### Events

The value of an `on*` attribute can be:

- a handler name: `onclick={save}`. The handler may take the DOM event
  as its single argument (`def save(e):`) or no arguments;
- a lambda: `onclick={lambda: remove(item)}`;
- any other expression, evaluated when the event fires:
  `onclick={remove(item)}`.

`onsubmit` automatically calls `preventDefault()`.

### Two-way binding

`bind={name}` works on `<input>`, `<textarea>` and `<select>`:

| Element | Bound property | Updates on |
|---|---|---|
| text-like `<input>`, `<textarea>` | `value` | every keystroke |
| `<input type="number">` with a numeric initial value | `value` as a number | valid numbers |
| `<input type="checkbox">` | `checked` (bool) | change |
| `<input type="radio" value="x">` | variable == `"x"` | change |
| `<select>` | `value` | change |

The variable is updated before any `oninput`/`onchange` handler on the
same element runs, so handlers see the new value.

### Control flow

`for` and `if`/`elif`/`else` lines whose bodies are markup are part of
the markup:

```pyweb
from pyweb import App

app = App()


@app.page("/")
def Home():
    todos = [{"title": "Write docs", "done": False}]
    n = len(todos)

    <ul>
        for i, todo in enumerate(todos):
            <li class={{"done": todo["done"]}}>{i + 1}. {todo["title"]}</li>
    </ul>
    if n == 0:
        <p>Nothing to do.</p>
    elif n == 1:
        <p>One thing to do.</p>
    else:
        <p>{n} things to do.</p>
```

Lists render with keyed updates: when the list changes, rows for items
that are still present keep their DOM nodes; only added rows render and
only removed rows are deleted.

## Components

```pyweb
from pyweb import App, component

app = App()


@component
def Card(title, subtitle="", children=None):
    <section class="card">
        <h2>{title}</h2>
        if subtitle:
            <p class="muted">{subtitle}</p>
        {children}
    </section>


@app.page("/")
def Home():
    <Card title="Welcome" subtitle="Components take props">
        <p>Nested markup arrives as children.</p>
    </Card>
```

- Lowercase tags are HTML elements; capitalised tags are components
  defined in the same file.
- Parameters are props. Parameters with defaults are optional; missing
  required props and unknown props are compile errors.
- A `children` parameter receives the nested markup.
- Components may have their own state and handlers, created per
  instance. Their initial state must be computable in the browser (pass
  server data in as props).
- Callback props are called like functions: `on_delete={...}` in the
  parent, `onclick={on_delete}` inside the component.

## Pages

```pyweb
from pyweb import App

app = App(title="Shop")


@app.page("/products/{product_id}", title="Product")
def Product(product_id: int):
    label = "Product #" + str(product_id)
    <h1>{label}</h1>
```

See [Pages, routing and assets](08-pages-routing-assets.md).

## Lifecycle hook

A handler named `on_mount` runs once in the browser after the page or
component is in the document. Use it for timers, focus, or loading data
after first paint:

```pyweb
from pyweb import App, server

app = App()


@server
def server_time() -> str:
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


@app.page("/")
def Clock():
    now = ""

    def refresh():
        now = server_time()

    def on_mount():
        refresh()
        setInterval(refresh, 5000)

    <p>Server time: {now}</p>
```

## Browser APIs

Browser code can use real JavaScript globals directly: `window`,
`document`, `console`, `localStorage`, `sessionStorage`, `navigator`,
`location`, `history`, `setTimeout`, `setInterval`, `clearTimeout`,
`clearInterval`, `fetch`, `alert`, `confirm`, `prompt`, `Math`, `JSON`,
`Date`, `Intl`, `URL`, `URLSearchParams`, `FormData`, `crypto`,
`performance`, `requestAnimationFrame` and a few more. Calls on them are
passed through unchanged (`localStorage.setItem("k", v)`).

Names in `pyweb.browser` (for example `from pyweb.browser import storage`)
map to their JavaScript counterparts as well.

The supported Python subset for browser code is described in
[Python in the browser](07-browser-python.md).
