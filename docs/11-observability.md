# 11 - Observability

> Source: `pyweb/observability.py` (tested in
> `tests/test_observability.py`).

## One trace per interaction

```python
from pyweb import observability as obs

trace_id = obs.context_from_headers(request.headers)  # joins browser trace
with tracer.span("db.query", table="users"):
    ...
headers = obs.inject_headers({})  # forward to downstream RPC
```

The browser runtime sends `traceparent` + `X-Request-Id`; the server
joins them, so button-click → RPC → DB → DOM update shares one trace.
`Tracer.start` picks up the ambient trace automatically via
contextvars — no manual threading through call stacks.

## Errors with codes, not stack dumps

```python
report = obs.error_report(exc)  # {"code", "message", "trace", ...}
```

`message` is browser-safe; internals stay server-side unless
`safe_detail=True` (dev/CLI only). Framework codes are registered:
`AuthError → unauthenticated`, `Forbidden → forbidden`,
`RPCError → rpc`, plus `bad-request` / `not-found` / `forbidden` /
`internal` for stdlib errors. Add yours with
`obs.register_error_code(MyError, "my-code")`.

## Time-travel log

```python
log = obs.EventLog()
log.record("signal", name="count", old=2, new=3)
ns = {}
log.replay(ns)  # ns == {"count": 3}
```

Bounded ring (10k events) — safe to leave on in staging; `since(seq,
kind=...)` powers the DevTools timeline.

## Inspect the magic

```bash
python -m pyweb.cli inspect app.pyweb --security
# page Home route=/ signals=['count']
#   browser        count  # reactive UI state read by markup...
#   browser        increment  # event handler reachable from UI...
#   browser+server __page__  # SSR initial HTML on server...
# rpc get_count() -> int [server] line 12
```
