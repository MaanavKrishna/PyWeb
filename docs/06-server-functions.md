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
- Arguments are checked against the type hints before your function
  runs, and anything wrong is a `validation_error` naming the argument:
  - `str`, `int`, `float` and `bool` are converted the way forms need
    (`"3"` → `3`), but a dict or list is never accepted for them;
  - `list[int]`, `dict[str, str]`, `Optional[...]`, `X | None` and
    `Literal["a", "b"]` are checked item by item;
  - `date`, `datetime` and `time` are parsed from ISO 8601 text, and
    `Decimal` and `UUID` from text;
  - a Model or dataclass parameter is built from an object with only its
    own fields (a browser can't set `id`, `private=True` or
    `readonly=True` fields); `Email` must look like one;
  - `Field(...)` rules (as the default, or in `Annotated[str, Field(max=40)]`)
    are checked too;
  - unknown and missing required arguments are refused.
- Return values are serialised to JSON (the same conversions as page
  state: dataclasses, `datetime`, `Decimal`, `to_dict()` objects).
- `async def` server functions are supported.
- Server calls cannot appear directly in markup expressions (`{load()}`):
  markup must be synchronous. Load data in a page variable or a handler.

## Who may call it

Options on `@server` check the caller before your code runs:

```python
@server(login=True)                               # signed in, or 401
def save_draft(text: str): ...

@server(roles=["admin"], fresh=600)               # the role (403 otherwise), signed in within 10 minutes
def delete_everything(): ...

@server(org_role=["owner", "admin"], feature="exports")   # a role in the current organization,
def export_projects(): ...                                 # and a plan with that feature

@server(rate="5/minute")                          # see "Limits for one function" below
def send_invite(email: str): ...
```

`login=`, `roles=` and `fresh=` come with the
[auth kit](10-auth.md); `org_role=` and `feature=` with
[teams](30-teams.md). The same options work on `@app.page` and
`@app.layout`. They decide who may call the function; which rows it may
touch is the job of [row policies](10-auth.md#row-policies) and
[tenant Models](30-teams.md#tenant-models).

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

A function that streams (below) answers `200` with
`Content-Type: application/x-ndjson`: one `{"chunk": value}` line per
`yield`, then `{"done": true}`, or `{"error": {...}}` if it raises part-way.

Every response carries `X-Request-Id` and a W3C `traceparent` header.
Because it is plain JSON over HTTP, server functions can also be called
from scripts, tests (`TestClient.rpc`) or other services.

## Streaming results

A server function that `yield`s sends each value to the browser as soon
as it is produced. Browser code reads it with `async for`:

```pyweb
import time

from pyweb import App, server

app = App()


@server
def progress(steps: int):
    for i in range(steps):
        time.sleep(0.5)           # slow work: a model, a big query, a file conversion
        yield {"done": i + 1, "of": steps}


@app.page("/")
def Job():
    status = "idle"
    job = None

    async def start():
        job = progress(10)
        async for update in job:
            status = f"{update['done']} of {update['of']}"
        status = "finished"

    def cancel():
        if job:
            job.cancel()

    <button onclick={start}>Start</button>
    <button onclick={cancel}>Cancel</button>
    <p>{status}</p>
```

- Calling a streaming function returns a stream; nothing is sent until
  `async for` reads it. Handlers that use `async for` are `async def`.
- `job.cancel()` stops it, and so does leaving the loop early with
  `break`. Either way the request is aborted and the generator on the
  server is closed: code after the current `yield` doesn't run, and
  `finally:` blocks and `with` statements clean up, which closes an
  upstream HTTP connection (an AI provider stops generating).
- If the function raises `RPCError` part-way, the `async for` raises it
  after the values sent so far. Other exceptions arrive as
  `RPCError("internal")` and are logged.
- `async def` generators work too. `session` and `request` work inside
  the generator. The per-call `rpc_timeout` doesn't apply to streams.
- `TestClient.rpc(...)` returns the list of values a stream sent.
- Stop streams a page started when the user navigates away, in
  `on_unmount`.

## Live updates

Server code can push messages to every browser showing a page. Three
functions from `pyweb` do it:

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
- **One connection per tab.** Every subscription and live query on a page
  shares one WebSocket (`/__pyweb/ws`), which reconnects with backoff and
  resumes where it stopped. Where WebSockets don't get through (a proxy
  that blocks them, an ASGI server without them) each subscription uses
  Server-Sent Events instead, and polling (`/__pyweb/poll`) as a last resort.
- **Feeds expire** after 24 hours; reloading the page makes a new one. A
  feed made while someone is signed in only works for that session, and
  stops when it ends.
- **One process or many.** Messages go through `pyweb.realtime`'s bus,
  in memory by default. With several processes set `PYWEB_REDIS_URL` and
  every process shares it.
- **Slow clients** can't make the server buffer without limit: a page that
  falls behind on a live query gets the current result once instead of
  every change, and one that stops reading is disconnected (it reconnects).

### Who's here: presence

`presence(name)` in a page returns a room token; `join(room, info,
on_members, on_cast)` in the browser joins it. `on_members(list)` runs
whenever someone joins or leaves, and `handle.cast(data)` sends `data` to
everyone else in the room (cursors, "typing..."), without storing it.

```pyweb
from pyweb import App, join, presence

app = App()


@app.page("/docs/{doc_id}")
def Doc(doc_id: int):
    room = presence(f"doc:{doc_id}")
    names = []
    me = None

    def members(people):
        names = [p["name"] for p in people]

    def on_mount():
        me = join(room, {"name": "Ada"}, members)

    <p>Here now: {", ".join(names)}</p>
```

Each member is the `info` the page joined with (at most 1 KB), plus
`"id"` (one per open page) and `"user"`: the signed-in user's id, set by
the server so a page can't pretend to be someone else. A member leaves
when its page closes, or within 45 seconds if its server goes away.
Presence uses the page's WebSocket; without one, `on_members` gets an
empty list.

## Limits and protection

`pyweb serve` and the ASGI adapter apply:

- a 1 MiB request body limit (`413`);
- a default rate limit of 120 RPC calls per minute per client;
- an optional per-call timeout (`rpc_timeout`, seconds);
- CSRF checks for authenticated calls when `PYWEB_CSRF_SECRET` is set;
- `POST` only for RPC endpoints (`405` otherwise).

### Limits for one function

Expensive or abusable functions (sending email, calling a paid API,
checking a code) can have their own limit:

```python
@server(rate="5/minute")                 # 5 calls a minute for each signed-in user
def send_invite(email: Email):
    ...

@server(rate="10/hour", per="ip")        # per client address, signed in or not
def check_coupon(code: str):
    ...

@server(rate="100/hour", per="org")      # one budget for each organization (with teams)
def generate_summary():
    ...

@server(rate="1000/day", per="all")      # every caller together
def generate_report():
    ...
```

`rate` is `calls/unit` with `second`, `minute`, `hour` or `day` (also
`"30/5m"`). `per="user"` (the default) counts each signed-in user, and
visitors who aren't signed in by address; `per="ip"` counts addresses,
`per="org"` the current organization and `per="all"` everyone together. Beyond the limit the call gets
`429 rate_limited` with `Retry-After`. With `PYWEB_REDIS_URL` set, the
counts are shared by every server process.

## Calling server functions from scripts and other services

Server functions are an HTTP API too: `POST /__pyweb/rpc/<name>` with
`{"args": {...}}`. For scripts, CI jobs and other services, create an
**API token** on `/account/tokens` (with the auth kit) and send it as a
bearer token:

```bash
curl https://example.com/__pyweb/rpc/add_project \
  -H "Authorization: Bearer pwk_..." \
  -H "Content-Type: application/json" \
  -d '{"args": {"name": "Rocket"}}'
```

A token acts as the user who made it (and their organization, with
teams), only for the server functions in its scopes (all of them unless
you list some), only for server function calls (never pages, forms or the
account), and never counts as a recent sign-in, so `fresh=` functions
refuse it. It can expire, and it keeps working when its user signs out
of their browsers. Revoke it on the same page. Only a hash is stored; the
token is shown once. From code: `auth.create_token(user, name, scopes,
org=, days=)`, `auth.tokens(user)`, `auth.revoke_token(user, id)`.

`pyweb openapi app.pyweb --out openapi.json` writes an OpenAPI 3.1
document for every server function: argument schemas from the type hints
and `Field` rules, summaries from docstrings, and which ones need a
sign-in, roles or a plan feature. Load it in Swagger UI or Postman, or
generate a client from it.
