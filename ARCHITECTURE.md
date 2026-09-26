# PyWeb Architecture

## Track B: Server Runtime + Database

Request lifecycle:

```
client -> Request -> middleware chain (request) -> route/RPC match
       -> middleware chain (response, reversed) -> Response + X-Request-Id
```

### Server (`pyweb/runtime/server`)

- `Request` / `Response`: tiny typed HTTP primitives. `Request` honors an
  incoming `X-Request-Id` header, else mints a 12-hex id; `handle()` always
  echoes it back as `x-request-id`.
- `Server`: route table (`route()`), typed RPC registry
  (`rpc()`, served at `POST /__pyweb/rpc/<name>` with `{"args", "kwargs"}`),
  `GET /__pyweb/health` endpoint, startup/shutdown hooks.
- `asgi()`: full ASGI app incl. lifespan (startup/shutdown) + http scope.
  `run(backend="auto")` uses uvicorn when installed, else the stdlib
  `serve()` adapter (`http.server`-based, `wsgi_like_handler()`).
- Structured JSON access logs: one JSON line per request on stdout
  (`ts, request_id, method, path, status, bytes, duration_ms, client_ip`).

### Middleware (in order)

1. `SecurityHeadersMiddleware` — CSP (`default-src 'self'`), `X-Frame-Options:
   DENY`, `X-Content-Type-Options: nosniff`, referrer-policy.
2. `GzipMiddleware` — compresses text-ish bodies >= 512 B when the client
   sends `Accept-Encoding: gzip`.
3. `SessionMiddleware` — signed-cookie sessions via stdlib `hmac`+SHA256
   (`value.hexsig`, `HttpOnly; SameSite=Lax`).
4. `CSRFMiddleware` — rejects RPC POSTs without a matching `X-CSRF-Token`
   header vs the session `csrf` token (403).
5. `RateLimitMiddleware` — per-IP sliding-window counter (default 100/60s,
   429 + `Retry-After`).
6. `StaticFilesMiddleware` (opt-in) — serves `{path: bytes}` with
   `hashed_url()` content-hash URLs (`app.<sha12>.js`) getting
   `Cache-Control: public, max-age=31536000, immutable`, plain paths
   `max-age=3600`, plus ETag/304 support.

### App + SSR (`pyweb/app.py`)

- `PyWeb`: owns a `Server` (default middleware wired with `secret`), `page()`
  decorator with `render="static" | "server" | "stream"`, `rpc()` with typed
  signature registry, `health()`.
- SSR: `layout()` wraps body HTML; `stream_chunks()` yields `<head>` first,
  then 1 KiB body chunks, then `</body></html>`. `render="stream"` responses
  are assembled from these chunks (chunk order covered by tests); `static`
  and `server` return the full layout document.

### Data (`pyweb/models.py`, `pyweb/db`, `pyweb/cache.py`)

- `Model`: `Field` descriptors (`Integer/Text/Real/Blob`, pk/nullable/
  default) collected by `ModelMeta`; `bind(db)`, `create/get/all/filter/
  save/delete`, `schema_sql()`. SQLite autoincrement PKs are read back via
  `lastrowid`.
- `Query`: builds PARAMETERIZED SQL only — values always become `?`
  (sqlite) / `%s` (postgres) placeholders; table/column names validated
  against `^[A-Za-z_][A-Za-z0-9_]*$`; unqualified UPDATE/DELETE refused.
- `SQLiteDB`: stdlib `sqlite3` with a pooled wrapper (single connection for
  `:memory:`) and `transaction()` (nested = join existing). Commits on
  success, rolls back on error.
- Migrations: `autogen(models, outdir)` diffs models to
  `migrations/schema.{up,down}.sql`; `apply(db, outdir, direction)` runs a
  direction inside one transaction.
- `PostgresDB`: psycopg adapter; raises a clear install error when psycopg
  is not importable.
- Cache: `MemoryCache` (TTL via monotonic clock + tag sets) and
  `RedisCache` (JSON/pickle values, per-tag sets; requires `redis` when
  used). `get_cache("memory" | "redis")` factory.

### Deploy (`pyweb/deploy.py`, docker/k8s artifacts only)

- `write_artifacts(outdir)`: emits `Dockerfile` (python:3.12-slim, probeable
  on 8000), `k8s-deployment.yaml` (2 replicas, readiness/liveness probes on
  `/__pyweb/health`), `k8s-service.yaml` (ClusterIP 80 -> 8000).
- `python -m pyweb.deploy serve` boots a default `PyWeb` app;
  `examples/todo` is the end-to-end CRUD demo; `examples/showcase/serve.py`
  is the demo `http.server`-backed wrapper.

### Security notes

- No string interpolation of values into SQL anywhere (builder +
  `sqlite3`/`psycopg` parameter binding); identifiers allowlisted.
- Sessions are HMAC-signed, CSRF tokens compared with `hmac.compare_digest`.
- Error paths return 4xx/500 JSON without tracebacks; access logs carry no
  bodies.
