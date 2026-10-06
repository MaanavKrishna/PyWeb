# Observability

PyWeb tells you what your app is doing without any setup. While you
develop, a toolbar on every page shows what each request did. In
production you get structured logs, Prometheus metrics, traces that
follow a click into the background jobs it queued, and a hook for error
reporting.

## The dev toolbar

`pyweb dev` puts a small badge in the corner of every page:

```text
⚡ 9.1 ms  1 SQL  +2  ● N+1?
```

That's how long the page took to render, how many queries it ran, how
many requests happened since (server function calls, form posts), and a
warning when something looks wrong. Click it to see, for the page and for
every request after it:

- **SQL**, with timings and parameters. When the same statement runs 10 or
  more times in one request it's shown once with a count, and flagged:
  that's almost always a loop that should use `include()`.
- **Spans** you added with `pyweb.telemetry.span` (below).
- **Side effects**: jobs queued, emails "sent".
- **The error**, if there was one, and the trace id.

The *Jobs* tab lists recent background jobs (and retries failed ones);
*Emails* shows the messages your app sent. In development emails are
captured there instead of being sent.

The toolbar exists only in the dev server. `pyweb build` and `pyweb serve`
never include it and its endpoints don't exist in production. Hide it
with `PYWEB_DEVTOOLS=0`.

## Logs

Servers and workers log to stderr: readable lines while developing, one
JSON object per line in production.

```json
{"ts": "2026-10-06T12:00:01.204+00:00", "level": "info", "logger": "pyweb.access",
 "msg": "POST /__pyweb/rpc/checkout 200 41.7ms (6 queries)", "method": "POST",
 "path": "/__pyweb/rpc/checkout", "status": 200, "ms": 41.7, "queries": 6, "query_ms": 3.9,
 "request_id": "4bf92f3577b34da6a3ce929d0e0e4736", "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
 "route": "rpc:checkout", "user": "42"}
```

Use Python's `logging` in your own code. Every line logged while handling
a request, or while running a job, gets the `request_id`, `trace_id`,
`route` and `user` automatically:

```python
import logging
log = logging.getLogger("shop")

@server
def checkout(cart_id: int):
    log.info("charging card", extra={"cart": cart_id, "amount": total})
```

- **One id per click.** The request id is the trace id. It's returned in the
  `X-Request-Id` header and shown on error pages, so a user's report leads
  straight to the log lines. A job queued by that request logs the same
  `trace_id`. Behind a proxy you run (`PYWEB_TRUST_PROXY`), an incoming
  `X-Request-Id` is used instead.
- **Secrets never reach the log.** Fields named like `password`, `token`,
  `secret`, `authorization`, `cookie`, `api_key`, `session`, `csrf` (add
  your own with `PYWEB_LOG_REDACT=iban,ssn`) are replaced with
  `[redacted]`. So are `name=value` pairs inside messages, URLs and
  tracebacks.
- **N+1 queries** turn the access line into a warning naming the query.

| Variable | Default | Meaning |
|---|---|---|
| `PYWEB_LOG_FORMAT` | `json` in production, else `text` | output format |
| `PYWEB_LOG_LEVEL` | `info` | `debug` also logs health checks and static files |
| `PYWEB_LOG_REDACT` | | more field names to redact, comma-separated |
| `PYWEB_ACCESS_LOG` | `1` | `0`: no line per request |

## Metrics

Every server answers `/metrics` in Prometheus' text format. In production
it needs a token, so it isn't public:

```bash
PYWEB_METRICS_TOKEN=$(python -c "import secrets; print(secrets.token_hex(24))")
```

```yaml
# prometheus.yml
scrape_configs:
  - job_name: shop
    authorization: {credentials: "<PYWEB_METRICS_TOKEN>"}
    static_configs: [{targets: ["shop:8000"]}]
```

Without a token, `/metrics` is a 404 in production. It's open while
developing. If your app has its own page at `/metrics`, that page wins;
the metrics are also at `/__pyweb/metrics`.

With `pyweb serve --workers 4`, each worker process writes its numbers to
a shared folder every few seconds, and whichever process answers the
scrape adds them all up. A scrape describes the whole server, not one
random process.

Background workers have no web server. Give them a metrics port:
`pyweb worker --metrics-port 9091` (or `PYWEB_METRICS_PORT`). Keep that
port private.

| Metric | Labels | What |
|---|---|---|
| `pyweb_http_requests_total` | method, route, status | requests answered |
| `pyweb_http_request_duration_seconds` | route | histogram |
| `pyweb_http_requests_in_flight` | | being handled now |
| `pyweb_rpc_errors_total` | function, code | server functions that failed |
| `pyweb_db_queries_total` | dialect | queries |
| `pyweb_db_query_duration_seconds` | | histogram |
| `pyweb_db_repeated_queries_total` | | requests with a likely N+1 |
| `pyweb_jobs_enqueued_total` | job | jobs queued |
| `pyweb_jobs_total` | job, outcome | runs: `done`, `retry`, `dead`, `timeout`, `lost` |
| `pyweb_job_duration_seconds` | job | histogram |
| `pyweb_jobs` | state | jobs waiting, running, failed, dead in the store |
| `pyweb_live_sockets` | | open live-update sockets |
| `pyweb_live_messages_total`, `pyweb_live_bytes_total` | | pushed to browsers |
| `pyweb_mail_total` | outcome | `sent`, `queued`, `failed` |
| `pyweb_auth_events_total` | kind, ok | sign-ins, sign-ups, resets, ... |
| `pyweb_errors_total` | where | unhandled errors (`rpc`, `page`, `form`, `job`) |
| `pyweb_build_info` | version | |

Routes are labelled by what handled the request, never by the raw path,
so the number of series stays small: `page:Home`, `rpc:checkout`, `form`,
`live`, `static`, `auth`, `not_found`. Any label is capped at 2,000
values per metric.

Your own metrics go into the same registry:

```python
from pyweb.telemetry.metrics import REGISTRY

orders = REGISTRY.counter("shop_orders_total", "Orders placed", ("plan",))
orders.inc(plan="pro")
```

## Traces

PyWeb follows [W3C Trace Context](https://www.w3.org/TR/trace-context/).
A `traceparent` header from the browser or a gateway is continued, and
each response carries its own. A job queued during a request stores the
request's trace and runs as part of it. One trace shows the click, the
server function, its queries, the job it queued and that job's queries,
even when a worker on another machine ran the job.

Mark the parts of your code you want to see:

```python
from pyweb.telemetry import span

@server
def checkout(cart_id: int):
    with span("charge card", provider="stripe"):
        ...
```

Spans show in the dev toolbar. With OpenTelemetry they're exported too.

### OpenTelemetry

```bash
pip install "pyweb-stack[otel]"
PYWEB_OTEL=1 OTEL_SERVICE_NAME=shop OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318 pyweb serve dist
```

`PYWEB_OTEL=1` sets up the SDK with the OTLP exporter, which reads the
standard `OTEL_*` variables. If your code configures a tracer provider
itself, PyWeb uses it and `PYWEB_OTEL` isn't needed. Requests become
`SERVER` spans named by route (`POST rpc:checkout`), queries become
`db SELECT` spans with the statement, and jobs become `CONSUMER` spans
under the request that queued them. Without OpenTelemetry configured,
nothing is exported and it costs nothing.

## Errors

`@app.on_error` gets every unhandled error from a page, server function,
form or job, with its context:

```python
@app.on_error
def report(error, info):
    # info: where ("rpc", "page", "form", "job"), request_id, trace_id, route, user,
    #       and rpc= / page= / job= / attempt=
    sentry_sdk.capture_exception(error)
```

Each error is reported once. A job is reported when it runs out of
attempts, not on every retry. A hook that raises is logged and skipped,
so it never breaks the request. The visitor sees a plain "internal server
error" with the request id. Details stay in your logs.
