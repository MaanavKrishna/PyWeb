# 14 — The `.pyweb` file format

`.pyweb` is Python plus markup. One file, two languages, zero ambiguity
about which line is which.

## The rule

Every line is classified exactly once:

| Line | Example | Meaning |
|------|---------|---------|
| Python | `count = 0`, `def increment():` | Normal Python. Scoping, imports, functions — all real, parsed by CPython `ast`. |
| Tag | `<button onclick={increment}>` | Markup. Tag name, attributes, `{expr}` holes. |
| Control over markup | `for todo in todos:` | A Python `for`/`if`/`elif`/`else` whose body is markup. Joins the UI tree *and* stays Python. |
| Content | `{count}`, `Count: {count}` | Inside an open tag: expressions or text rendered into the element. |

Markup lines are replaced with `__pyweb_ui__(<lineno>)` placeholders so
`ast` parses real scoping; the UI tree is rebuilt from line numbers.
Nothing is `eval`'d at parse time.

## Expressions in markup

- `{expr}` — any Python expression: `{count}`, `{user.name}`,
  `{"Following" if following else "Follow"}`.
- `attr={name}` — event handlers and bindings: `onclick={increment}`,
  `bind={search}`.
- `attr="literal"` / `attr='literal'` — static strings pass through.

## Components

A `@component`-decorated (or plain) function returning markup is a
component. Props are function parameters with type annotations; the
compiler type-checks call sites:

```python
@component
def UserCard(user: User):
    following = False

    def toggle():
        following = not following

    <article class="user-card">
        <h2>{user.name}</h2>

        if user.admin:
            <Badge>Admin</Badge>

        <button onclick={toggle}>
            {"Following" if following else "Follow"}
        </button>
    </article>
```

## Pure-Python alternative

Markup is optional. Any page or component can return element calls:

```python
def Home():
    return Page(
        Heading("Hello"),
        Button("Click"),
    )
```

Same reactive graph, same SSR, no angle brackets.

## File discovery

- Pages: any function decorated with `@app.page("/route")`.
- Route params: `/users/{user_id}` binds to the function parameter and is
  validated against its annotation.
- Server boundary: `@server` / `@browser` / `@edge` / `@worker` /
  `@shared` override placement inference (see `03-rpc-placement.md`).
- Imports of server-only packages (`psycopg`, etc.) pin the enclosing
  module server-side — the compiler errors instead of bundling secrets.

## What is NOT supported

- Arbitrary HTML inside Python expressions (markup lives on its own
  lines; use `{expr}` holes for values).
- Multi-line Python statements split across markup lines — finish the
  statement, then write markup.
- `<script>` passthrough with inline JS — use the `npm` escape hatch or
  `pyweb.browser` bindings instead (see `07-escape-hatches.md`).
