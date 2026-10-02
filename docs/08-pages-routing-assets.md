# Pages, routing and assets

## Pages and routes

```pyweb
from pyweb import App, NotFound

app = App(title="Library", stylesheets=["/static/app.css"], lang="en")
BOOKS = {1: "Dune", 2: "Emma"}


@app.page("/")
def Home():
    <ul>
        for book_id, title in BOOKS.items():
            <li><a href={"/books/" + str(book_id)}>{title}</a></li>
    </ul>


@app.page("/books/{book_id}", title="Book")
def Book(book_id: int):
    if book_id not in BOOKS:
        raise NotFound()
    title = BOOKS[book_id]

    <h1>{title}</h1>
    <a href="/">Back</a>
```

- `{name}` segments become parameters of the page function. Annotate
  them as `int` or `float` to convert; a value that doesn't convert is a
  404.
- `title=` on `@app.page` sets the `<title>`; otherwise the app title is
  used.
- Navigation between pages is ordinary links (full page loads). Each
  page ships only its own small module plus the shared, cached runtime.
- Routes are matched in definition order; unmatched paths get a 404
  page. `pyweb dev` shows Python tracebacks for errors in pages; `serve`
  shows a generic 500 page with a request id and logs the traceback.

## Responses other than HTML

Return `redirect(url)` from a page to send `303 See Other`. Raise
`NotFound()` for 404. Raise `RPCError("forbidden")` or
`RPCError("unauthenticated")` for 403/401. Anything else that escapes a
page is a 500.

## Page-level Python

A page function body runs on the server for every request, top to
bottom, until the markup. You can use any Python there: queries,
`if` statements, loops, early returns. Values assigned that way are
computed on the server; see [State and reactivity](05-reactivity.md).

## Static files and stylesheets

Put files in a `static/` folder next to `app.pyweb`. They are served at
`/static/...` by `pyweb dev`, copied into `dist/static/` by
`pyweb build`, and served with long-lived caching by `pyweb serve`.

```text
myapp/
  app.pyweb
  static/
    app.css
    logo.svg
```

Add stylesheets for every page with `App(stylesheets=[...])`. Any CSS
approach works: hand-written CSS, a CSS framework's built file, or
Tailwind's CLI output written into `static/`.

## The HTML document

Every page response is:

```html
<!doctype html>
<html lang="en"><head>…title, stylesheets…</head>
<body>
  <div data-pw-root="Home"> …server-rendered markup… </div>
  <script id="pw-state" type="application/json">{…state the browser reads…}</script>
  <script type="module" src="/static/Home.<hash>.js"></script>
</body></html>
```

Pages with no interactivity have neither script tag.
