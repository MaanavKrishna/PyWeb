# Server toolkit and API stability

Besides the compiler and server, PyWeb includes small, dependency-free
libraries for common server-side needs. Use them from page bodies and
`@server` functions.

## Caching

```python
from pyweb.cache import cache, get_cache

@cache(minutes=5, tags=["products"])
def product_list():
    return expensive_query()

store = get_cache("memory")            # or get_cache("redis", url="redis://...")
store.set("k", {"v": 1}, ttl=60, tags=["user:7"])
store.invalidate_tag("user:7")
```

## Background jobs

```python
from pyweb.jobs import task

@task(retries=2)
def send_report(email, _job=None):     # accept _job to report progress
    _job.set_progress(0.5)
    ...

job = send_report("ada@example.com")   # returns immediately; runs in a worker thread
```

`pyweb.jobs.RedisQueue(url)` distributes jobs across processes: one
process submits, any process calling `queue.drain()` runs them, and
`queue.status(job_id)` reads the result from any process.

## Realtime fan-out

```python
from pyweb.realtime import RedisBus

bus = RedisBus("redis://localhost:6379/0")
bus.publish("room:python", {"author": "ada", "text": "hi"})
bus.since("room:python", last_id=0)    # [(1, {...}), ...] shared across processes
```

Browsers receive updates by polling a server function (see the chat
example) or via `/__pyweb/poll?channel=NAME&since=ID`.

## Uploads, forms, observability

- `pyweb.uploads.validate_upload(filename=, size=, content_type=,
  allowed_types=, max_bytes=)` and `safe_filename`.
- `pyweb.forms.validate(model, data)` / `fields_for(model)`.
- `pyweb.observability.Logger`, `Tracer`, `Metrics`, `format_error`.
- `pyweb.security.escape`, `safe_join`, `safe_next`.

## Stability

PyWeb follows [semantic versioning](https://semver.org). From 1.0, the
following are **stable**: breaking changes only in a new major version,
with deprecation warnings for at least one minor release first.

| Stable | |
|---|---|
| The `.pyweb` language | markup, expressions, attributes, events, `bind`, control flow, components, `on_mount`, state classification rules |
| `pyweb` | `App`, `server`, `component`, `request`, `session`, `redirect`, `NotFound`, `RPCError` |
| RPC wire protocol | URL, request/response JSON, error codes |
| `pyweb.testing` | `TestClient`, `serve` |
| `pyweb.asgi` | `create_app` |
| `pyweb.db`, `pyweb.db.migrate` | `connect`, `execute`, `transaction`, `stream`, `Query`, migrations |
| `pyweb.auth` | passwords, sessions, TOTP, magic links, WebAuthn assertion verification |
| `pyweb.cache`, `pyweb.jobs`, `pyweb.realtime` | as documented above |
| CLI | `new dev check inspect build serve db deploy` and their documented flags |
| `dist/` layout | `app.pyweb`, `manifest.json` keys documented in [Deployment](12-deployment.md) |

**Experimental** (importable, tested, but may change in any release):
`pyweb.models` (ORM-style models), `pyweb.live` (live queries),
`pyweb.sync` (offline sync), `pyweb.css` helpers, `pyweb.plugins`,
`pyweb.platform`, `pyweb.lsp` (editor helpers; no language server is
shipped yet), `pyweb.npm` (TypeScript declaration stub generator; npm
packages cannot yet be imported into browser code), `pyweb.browser`
server-side stubs, the `pyweb.compiler` internals and the generated
JavaScript.
