# Server functions and RPC

A module-level function decorated with `@server` runs only on the
server. Browser code calls it like any function; the compiler turns the
call into an awaited HTTP request and the enclosing handler into an
`async` function.

```pyweb
from pyweb import App, RPCError, server

app = App()
STOCK = {"apple": 3, "pear": 0}


@server
def buy(item: str, quantity: int = 1) -> dict:
    if STOCK.get(item, 0) < quantity:
        raise RPCError("conflict", f"Only {STOCK.get(item, 0)} {item}(s) left.")
    STOCK[item] -= quantity
    return {"item": item, "left": STOCK[item]}


@app.page("/")
def Shop():
    message = ""

    def purchase(item):
        try:
            result = buy(item, quantity=1)
            message = f"Bought one {item}; {result['left']} left."
        except RPCError as e:
            message = str(e)

    <button onclick={purchase("apple")}>Buy an apple</button>
    <button onclick={purchase("pear")}>Buy a pear</button>
    <p>{message}</p>
```

## Rules

- Parameters are matched by name; positional and keyword calls both
  work. `*args`/`**kwargs` are not allowed on `@server` functions.
- Arguments are validated against annotations before your function runs:
  `int`, `float` and `bool` are coerced (`"3"` → `3`); a failed coercion
  or a missing required argument is a `validation_error`. Other
  annotations are documentation.
- Return values are serialised to JSON (the same conversions as page
  state: dataclasses, `datetime`, `Decimal`, `to_dict()` objects).
- `async def` server functions are supported.
- Server calls cannot appear directly in markup expressions (`{load()}`):
  markup must be synchronous. Load data in a page variable or a handler.

## Errors

Raise `pyweb.RPCError(code, message)` for an error the browser should
handle. The code maps to an HTTP status and is available in the browser
as `e.code`; `str(e)` is the message.

| Code | Status | Use for |
|---|---|---|
| `validation_error` | 422 | bad input (also raised automatically) |
| `unauthenticated` | 401 | not signed in (`session.require()` raises it) |
| `forbidden` | 403 | signed in, not allowed |
| `not_found` | 404 | missing resource |
| `conflict` | 409 | state changed underneath the caller |
| `rate_limited` | 429 | too many calls |
| `timeout` | 504 | exceeded the server's RPC timeout |
| `internal` | 500 | any other exception (details are logged, not sent) |

`ValueError` and `TypeError` raised by your function are reported as
`validation_error` with their message. Any other exception is reported
as `internal` with a generic message; the traceback goes to the server
log with the request id.

In page functions, `RPCError("not_found")`, `("forbidden")` and
`("unauthenticated")` turn into 404, 403 and 401 responses.

## Request context

Inside server functions and page bodies:

```python
from pyweb import request, session, redirect, NotFound

request.path            # "/products/3"
request.method          # "GET" / "POST"
request.query           # {"page": "2"}
request.headers         # dict
request.cookies         # dict
request.id              # request id (also sent as X-Request-Id)
request.set_cookie("theme", "dark", max_age=86400)

session.user()          # {"sub": user_id, ...} or None
session.login(user_id, roles=["admin"])
session.logout()
session.require("admin")   # returns the session or raises RPCError

raise NotFound()        # in a page: respond 404
redirect("/login")      # return from a page: respond 303
```

## The wire protocol

```text
POST /__pyweb/rpc/<name>
Content-Type: application/json
X-CSRF-Token: <token>          (when CSRF protection is configured)

{"args": {"item": "apple", "quantity": 1}}

200 {"result": ...}
4xx/5xx {"error": {"code": "conflict", "message": "...", "details": {}}}
```

Every response carries `X-Request-Id` and a W3C `traceparent` header.
Because it is plain JSON over HTTP, server functions can also be called
from scripts, tests (`TestClient.rpc`) or other services.

## Live updates

Server code can push messages to every browser showing a page, over
Server-Sent Events. Three functions from `pyweb` do it:

- `publish(name, data)` in server code sends `data` (anything
  JSON-serialisable) to channel `name`.
- `channel(name)` in a page function returns a *feed*: a signed token
  that lets that page listen to channel `name`. Only visitors who were
  served the page get it, so the page decides who may listen.
- `subscribe(feed, handler)` in browser code (usually `on_mount`) calls
  `handler(message)` for every message. The handler runs like an event
  handler, so assigning page variables updates the page.

```pyweb
from pyweb import App, channel, publish, server, subscribe

app = App()
_scores = {"home": 0, "away": 0}


@server
def score(team: str) -> None:
    _scores[team] += 1
    publish("scores", _scores)


@app.page("/")
def Scoreboard():
    scores = dict(_scores)
    feed = channel("scores")

    def changed(new_scores):
        scores = new_scores

    def on_mount():
        subscribe(feed, changed)

    <main>
        <p id="home">Home {scores["home"]}</p>
        <p id="away">Away {scores["away"]}</p>
        <button onclick={score("home")}>Home scores</button>
    </main>
```

How it behaves:

- **Nothing is missed.** A feed remembers the channel's position when the
  page rendered, so messages published before the browser connects are
  still delivered, and a browser that reconnects resumes after the last
  message it saw.
- **It falls back to polling** (`/__pyweb/poll`) if the stream can't be
  opened, for example behind a proxy that refuses streaming responses.
- **Feeds expire** after 24 hours; reloading the page makes a new one.
- **One process or many.** Messages go through `pyweb.realtime`'s bus,
  in memory by default. With several processes, share it through Redis
  once at startup: `realtime.use_bus(RedisBus("redis://..."))`.
- **Servers.** `pyweb dev`, `pyweb serve` and the ASGI adapter all stream.
  Each open page holds one connection; streams close after five minutes
  and browsers reconnect on their own. Behind nginx, responses carry
  `X-Accel-Buffering: no` so they aren't buffered.

## Limits and protection

`pyweb serve` and the ASGI adapter apply:

- a 1 MiB request body limit (`413`);
- a default rate limit of 120 RPC calls per minute per client;
- an optional per-call timeout (`rpc_timeout`, seconds);
- CSRF checks for authenticated calls when `PYWEB_CSRF_SECRET` is set;
- `POST` only for RPC endpoints (`405` otherwise).
