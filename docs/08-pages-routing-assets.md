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
- Other parameters come from the query string; see below.
- `title=` on `@app.page` sets the `<title>`; otherwise the app title is
  used.
- Links between pages are ordinary `<a href>` links. In apps with
  interactive pages they load without a full page reload, and shared
  layouts stay in place; see [Layouts & navigation](19-layouts-navigation.md).
  Each page ships only its own small module plus the shared, cached
  runtime.
- Routes are matched in definition order; unmatched paths get a 404
  page. `pyweb dev` shows Python tracebacks for errors in pages; `serve`
  shows a generic 500 page with a request id and logs the traceback.
  Both can be replaced with your own pages (below).

## Query parameters

Page parameters that aren't in the route are read from the query
string and converted to their annotated type:

```pyweb
from pyweb import App

app = App()
BOOKS = ["Dune", "Emma", "Ulysses"]


@app.page("/search")
def Search(q: str = "", page: int = 1, tags: list[str] = [], exact: bool = False):
    found = [b for b in BOOKS if (b == q if exact else q.lower() in b.lower())]

    <h1>Results for {q} (page {page})</h1>
    <ul>
        for title in found:
            <li>{title}</li>
    </ul>
```

`/search?q=em&page=2&tags=a&tags=b&exact=no` calls
`Search(q="em", page=2, tags=["a", "b"], exact=False)`.

- `int`, `float` and `bool` values are converted (`bool` accepts
  `1/0`, `true/false`, `yes/no`, `on/off`). `list[...]` collects every
  value of a repeated key.
- Parameters with a default are optional. A missing required one, or a
  value that doesn't convert, answers **400** with a message saying
  which. (Route segments that don't convert answer 404 instead.)
- `pyweb.request.query` still gives the raw values as a dict.

## Titles, descriptions and social cards

```pyweb
from pyweb import App, head

app = App(title="Library", base_url="https://books.example",
          description="A small library", image="/static/cover.png")
BOOKS = {1: {"title": "Dune", "blurb": "Spice and sand."}}


@app.page("/about", title="About us", description="Who runs the library")
def About():
    <p>About</p>


@app.page("/books/{book_id}")
def Book(book_id: int):
    book = BOOKS[book_id]
    head(title=book["title"] + " - Library", description=book["blurb"])

    <h1>{book["title"]}</h1>
```

- `description=`, `image=` and `noindex=True` on `@app.page` add
  `<meta name="description">`, Open Graph and Twitter card tags, and a
  `robots` tag. The app's `description` and `image` are the defaults.
- `head(title=..., description=..., image=..., canonical=..., noindex=...)`
  sets them from page code, once the data is loaded. It runs on the
  server; layouts can call it too, and the page's call wins.
- With `App(base_url=...)` every page gets `<link rel="canonical">` and
  `og:url` for its path (without the query string), and relative image
  paths become absolute, which social sites need. `canonical=False` on a
  page turns it off; `canonical="/other"` points elsewhere.

## Error pages

Write the 404 and 500 pages (or any status) as pages:

```pyweb
from pyweb import App

app = App()


@app.error(404)
def Missing(path):
    <h1>Nothing at {path}</h1>
    <a href="/">Home</a>


@app.error(500)
def Broken(request_id):
    <h1>Something went wrong</h1>
    <p>Quote this id if you contact us: {request_id}</p>
```

- Error pages can take any of `path`, `status`, `message` and
  `request_id`. They use root layouts (prefix `/`) like other pages.
- A 500 page also covers other 5xx errors. `pyweb dev` still shows the
  traceback for 500s; `pyweb serve` shows your page.
- If an error page itself fails, the built-in page is used.

## Responses other than HTML

Return `redirect(url)` from a page to send `303 See Other`. Raise
`NotFound()` for 404. Bad query parameters answer 400. Raise `RPCError("forbidden")` or
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

Pages with no interactivity have neither script tag. Layouts add their
own root element around the page's, with their own state script and
module.
