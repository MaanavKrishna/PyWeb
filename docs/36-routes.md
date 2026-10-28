# Routes & middleware

Pages answer with HTML a person reads. Everything else your app answers, a JSON API, a CSV
download, a feed, a callback from another service, is a **route**: a plain Python function at
a URL. **Middleware** runs around every request, for timing, headers, logging or answering early.

```python
from pyweb import App
from pyweb.routes import csv, json

app = App(title="Shop", base_url="https://shop.example")


@app.route("/api/products/{id:int}")
def product(id: int):
    p = Product.get(id)
    return p.to_dict() if p else json({"error": "not found"}, status=404)


@app.route("/api/products", methods=["POST"], login=True)
def add_product(body: dict):
    return json(Product.create(name=body["name"]).to_dict(), status=201)


@app.route("/admin/products.csv", roles=["admin"])
def export():
    return csv(Product.all(), filename="products.csv")
```

Routes live in your `.pyweb` file (or a module it imports) like server functions, and like them
they never reach the browser.

## Paths

| Pattern | Matches | The handler gets |
|---|---|---|
| `/items/{id:int}` | `/items/42` | `id=42` |
| `/posts/{slug}` | `/posts/hello` (one segment) | `slug="hello"` |
| `/files/{rest:path}` | `/files/a/b/c.txt` | `rest="a/b/c.txt"` |
| `/{id:uuid}`, `/{x:float}` | a UUID, a number | `uuid.UUID`, `float` |

A path that doesn't convert (`/items/abc` for `{id:int}`) doesn't match. Routes are checked
before pages; the same path can't be both. A path that matches with the wrong method answers
`405` with an `Allow` header. `@app.get(path)` and `@app.post(path)` are short for one method.

## What the handler gets and returns

Parameters are filled by name: path parameters, query parameters (`?q=...`, typed by the
annotation: `int`, `float`, `bool`, else `str`; a missing one without a default is `400`),
`body` (the JSON body, or a form's fields as a dict) and `request` (the request itself).
`pyweb.request` and `pyweb.session` work as in server functions.

| Return | Response |
|---|---|
| a `dict` or `list` | JSON |
| a `str` | `text/plain` (or the route's `content_type=`, e.g. `"text/html; charset=utf-8"`) |
| `bytes` | a download (`application/octet-stream`) |
| `None` | `204 No Content` |
| `json(data, status=)`, `text(...)`, `html(...)`, `csv(rows, filename=)` | exactly that |
| `redirect("/somewhere")` | `303` |
| `Response(status, body, headers)` | as is |

Raise `NotFound` for a 404 and `RPCError` for the others; anything else is a 500 (and is
reported like a page error). `csv()` takes dicts, Models or lists; text starting with `=`, `+`,
`-` or `@` is prefixed with `'` so spreadsheets don't run it as a formula.

## Who can call it

| Option | Effect |
|---|---|
| `login=True` | signed in, else `401` |
| `roles=["admin"]` | those roles, else `403` |
| `fresh=600` | signed in within the last 600 seconds |
| `rate="30/minute"`, `per="user"` | limited like a server function (`429` with `Retry-After`) |
| `tokens=True` | API tokens (`Authorization: Bearer pwk_...`) work here; the token's scopes name the route |
| `cors=["https://partner.example"]` | those origins may call it from their pages (preflight answered) |
| `csrf=False` | skip the same-site check below (a public endpoint) |

A request that changes something (POST, PUT, PATCH, DELETE) and carries the visitor's cookies
must come from your own site: browsers say where a request came from, and PyWeb refuses one from
another site. API tokens and the `cors=` origins are the ways in from elsewhere.

## Middleware

```python
import time

@app.middleware
def timing(request, call_next):
    start = time.perf_counter()
    response = call_next(request)
    response.headers["Server-Timing"] = f"app;dur={(time.perf_counter() - start) * 1000:.1f}"
    return response


@app.middleware
def maintenance(request, call_next):
    if MAINTENANCE and not request.path.startswith("/healthz"):
        return json({"error": "back soon"}, status=503)
    return call_next(request)
```

Middleware runs for pages, routes, server functions and forms (not static files), in the order
it was added: the first one added is the outermost. Return a response to answer without calling
`call_next`.

## robots.txt and sitemap.xml

`/robots.txt` allows everything except PyWeb's own endpoints and `/admin`, and points to the
sitemap when the app has a `base_url`. `App(robots="...")` replaces the text; `robots=False`
turns it off. `/sitemap.xml` (with `base_url`) lists every page without parameters that anyone
may see (no `login=`, no `noindex`); add the others with a generator:

```python
@app.sitemap
def posts():
    return [f"/blog/{p.slug}" for p in Post.where(published=True)]
```

A route or page of your own at either path wins.

## Plugins

A `Plugin` can declare routes too (`@plugin.route(...)`, and `@plugin.page(...)` for HTML it
builds itself); `App(plugins=[...])` registers them with its server functions and Models.

## In the tools

`pyweb openapi` describes routes next to server functions, and the MCP `pyweb_routes` tool lists
them.
