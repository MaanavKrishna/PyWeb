# State and reactivity

You never declare reactive state. The compiler reads each page (and
component) function and classifies every local variable by **how it is
used**.

## The three kinds of state

| Kind | Rule | In the browser |
|---|---|---|
| **signal** | changed by an event handler (assigned, `+=`, mutated with `.append()`, item assignment, `del x[i]`, …) or bound with `bind={x}` | a reactive cell; writing it updates exactly the DOM that reads it |
| **computed** | assigned once from an expression that reads signals, never changed by a handler | a cached derivation, recomputed only after one of its inputs changes |
| **constant** | anything else | a plain value |

```pyweb
from pyweb import App

app = App()


@app.page("/")
def Cart():
    price = 25            # constant
    quantity = 1          # signal: bound to the input
    total = price * quantity          # computed: reads a signal
    label = "Total"       # constant

    <label>Quantity <input type="number" bind={quantity} /></label>
    <p>{label}: {total}</p>
```

Typing in the input updates `quantity`, which invalidates `total`, which
updates one text node. Nothing else re-renders. There is no virtual DOM.

## Where initial values come from

Separately, each value's *origin* decides where it is first computed:

| Origin | Example | Behaviour |
|---|---|---|
| literal | `count = 0` | known at compile time |
| browser | `total = price * quantity` | computed by the generated JavaScript |
| server | `rows = load_rows()`, `user = session.user()`, anything after page-level Python logic (`if`, loops), anything derived from another server value | computed by Python on every request |

Server values are rendered into the HTML. Only the ones that browser
code (markup or handlers) actually reads are serialised to JSON for the
page, so intermediate values such as a full user record stay private.
The serialised state must be JSON-compatible; `dict`, `list`, `str`,
numbers, `bool`, `None`, dataclasses, `datetime`/`date` (ISO strings),
`Decimal` (float) and objects with `to_dict()`/`model_dump()` are
converted automatically.

## Updating state in handlers

Inside a handler, page variables are shared with the page: assigning
one updates the page state. (In plain Python this would create a local;
in a PyWeb page it is the point.) Names that are not page variables are
ordinary locals.

```pyweb
from pyweb import App

app = App()


@app.page("/")
def Board():
    cards = [{"title": "a", "votes": 0}]
    selected = None

    def vote(i):
        cards[i]["votes"] += 1          # item update: copy-on-write
        selected = i                    # plain assignment

    def sort_cards():
        cards.sort(key=lambda c: -c["votes"])

    <ul onclick={sort_cards}>
        for i, card in enumerate(cards):
            <li onclick={vote(i)}>{card["title"]}: {card["votes"]}</li>
    </ul>
```

Mutations of state (`append`, `extend`, `insert`, `pop`, `remove`,
`clear`, `sort`, `reverse`, `update`, `setdefault`, `add`, `discard`,
`x[i] = v`, `x[k][j] = v`, `x.attr = v`, `del x[i]`) are compiled to
copy-on-write updates: the changed container and every container on the
path to it are copied, siblings are shared. List rows keyed on the
changed item re-render; the rest keep their DOM.

Assigning to a computed value or constant from a handler is a compile
error, because the value would immediately be recomputed or ignored.

## Reacting to changes

Markup updates by itself. For anything else that should follow a value
(redrawing a chart, saving a draft, scrolling a log), call
`watch(lambda: value, handler)` from `on_mount`. `handler(new_value)`
runs each time the value changes, not for the current value:

```pyweb
from pyweb import App
from pyweb.browser import watch

app = App()


@app.page("/")
def Notes():
    draft = ""

    def on_mount():
        draft = localStorage.getItem("draft") or ""
        watch(lambda: draft, save)

    def save(text):
        localStorage.setItem("draft", text)

    <textarea bind={draft}></textarea>
```

Watches, `subscribe(...)` calls and other cleanups set up in `on_mount`
stop when the page is left (see
[Layouts & navigation](19-layouts-navigation.md)).

## Batching and ordering

All writes made synchronously in one event handler are batched: each
affected binding updates once, after the handler returns (or reaches
its first `await`, such as a server call). Reads inside the handler see
the latest values immediately.

## Rendering model

1. The server renders the page to HTML with real values.
2. The page module **hydrates** it: it walks the server's DOM in order and
   attaches event handlers and live bindings to the existing nodes. No
   node is recreated or moved, so focus, the caret position and anything
   the user typed before the module loaded are kept, and text typed into
   a bound field is copied into its variable.
3. From then on each signal write re-runs only the bindings that read it.

If the server HTML doesn't match what the browser code would render (for
example, a value that formats differently in Python and JavaScript, or
HTML the browser's parser restructures, like a `<div>` inside a `<p>`),
hydration stops and the page is rendered from scratch on the client, as
earlier versions always did. The browser console shows a warning that
names the first difference, and the page root gets
`data-pw-mode="rendered"` instead of `"hydrated"`.
