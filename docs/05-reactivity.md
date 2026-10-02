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

## Batching and ordering

All writes made synchronously in one event handler are batched: each
affected binding updates once, after the handler returns (or reaches
its first `await`, such as a server call). Reads inside the handler see
the latest values immediately.

## Rendering model

1. The server renders the page to HTML with real values.
2. The page module rebuilds the same DOM with live bindings and swaps it
   in. This takes a few milliseconds and does not change what is
   displayed.
3. From then on each signal write re-runs only the bindings that read it.

Input that a user types into a field *before* the module has loaded is
replaced by the server value when the page takes over. Modules load in
parallel with the HTML and are small, so this window is short, but it
exists.
