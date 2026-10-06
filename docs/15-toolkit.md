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

Use durable jobs: `@app.job` and `fn.enqueue(...)`, `@app.cron(...)`,
`pyweb worker`. See [Background jobs](24-jobs.md).

The 0.4 in-memory `@task` still works (it runs in a thread of the process
that called it, and is lost on restart):

```python
from pyweb.jobs import task

@task(retries=2)
def send_report(email, _job=None):     # accept _job to report progress
    _job.set_progress(0.5)
    ...

job = send_report("ada@example.com")   # returns immediately; runs in a worker thread
```

## Realtime fan-out

```python
from pyweb.realtime import RedisBus

bus = RedisBus("redis://localhost:6379/0")
bus.publish("room:python", {"author": "ada", "text": "hi"})
bus.since("room:python", last_id=0)    # [(1, {...}), ...] shared across processes
```

`pyweb.publish(name, data)` publishes through this bus, and pages listen
with `channel()` and `subscribe()` (see
[Live updates](06-server-functions.md#live-updates)). To share one bus
between processes, call `realtime.use_bus(bus)` at startup.

## Uploads, forms, observability

- `pyweb.uploads.validate_upload(filename=, size=, content_type=,
  allowed_types=, max_bytes=)` and `safe_filename`.
- `pyweb.forms.validate(model, data)` / `fields_for(model)`.
- `pyweb.observability.Logger`, `Tracer`, `Metrics`, `format_error`.
- `pyweb.security.escape`, `safe_join`, `safe_next`.

## Stability

PyWeb follows [semantic versioning](https://semver.org). It is in the
0.x series, so minor releases (0.2, 0.3, …) may still contain breaking
changes. For the APIs marked **stable** below, any breaking change is
listed in the changelog and, where possible, preceded by a deprecation
warning in an earlier release. They are the intended 1.0 API.

| Stable (intended 1.0 API) | |
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
`pyweb.models` (ORM-style models), `pyweb.livetable` (in-memory live tables; it was `pyweb.live` before 0.4),
`pyweb.sync` (offline sync), `pyweb.css` helpers, `pyweb.plugins`,
`pyweb.platform`, `pyweb.lsp` (its Python functions; the `pyweb lsp`
command itself is supported), `pyweb.dts` (TypeScript declarations to Python dataclasses;
it was `pyweb.npm` before 0.4), `pyweb.browser`
server-side stubs, the `pyweb.compiler` internals and the generated
JavaScript.
