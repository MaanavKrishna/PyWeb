# Deployment

The quickest way to production is `pyweb deploy`: it reads your app,
prints a plan (database, worker, Redis, migrations, HTTPS) and writes the
files for Docker, Compose, Kubernetes, Fly, Render or Railway. See
[Scaling & deploying](25-scaling.md). This page covers what those files
run: the build, the server, its settings and the proxy in front of it.

## Build

```bash
pyweb build app.pyweb --out dist --production
```

`dist/` is self-contained:

```text
dist/
  app.pyweb              your app (server functions and page bodies run from it)
  manifest.json          routes, asset names, RPC table, byte sizes
  Dockerfile             a ready-to-use image definition
  static/
    runtime.<hash>.js    shared browser runtime (~14 KB gzip)
    <Page>.<hash>.js     one module per interactive page
    <Layout>.<hash>.js   one module per interactive layout
    markdown.<hash>.js   only when a page uses <Markdown>
    vendor/              npm packages from pyweb.lock (see npm packages)
    ...                  your static/ folder
  server/
    <Page>.html          static prerender (for hosting pages with no server data on a CDN)
```

Without `--production`, files keep readable names and are not minified.

## Serve

### PyWeb's server

```bash
PYWEB_ENV=production PYWEB_AUTH_SECRET=... pyweb serve dist --host 0.0.0.0 --port 8000 --workers 4
```

PyWeb's own server, standard library only: HTTP/1.1 with keep-alive,
WebSockets and Server-Sent Events on one asyncio event loop per process,
so thousands of open live connections cost a coroutine each rather than a
thread. Page renders and server functions run on a thread pool
(`PYWEB_THREADS`, default 32 per process). `--workers N` (or
`WEB_CONCURRENCY`) starts N processes sharing the port and replaces any
that die.

It refuses ambiguous requests that could let a proxy and the server
disagree about where a request ends (request smuggling): both
`Content-Length` and `Transfer-Encoding`, repeated lengths, folded
headers, bad chunk sizes. It times out slow request heads, bodies and
readers, caps connections, serves hashed assets with
`Cache-Control: immutable`, and on `SIGTERM` stops accepting, finishes
in-flight requests and tells open pages to reconnect elsewhere. Put it
behind a reverse proxy (nginx, Caddy, a cloud load balancer) for TLS.

### ASGI (uvicorn, gunicorn, hypercorn)

```python
# asgi.py
from pyweb.asgi import create_app

app = create_app("dist")          # or "app.pyweb" to compile at startup
```

```bash
pip install "pyweb-stack[asgi]"
uvicorn asgi:app --host 0.0.0.0 --port 8000 --workers 4
```

Use this when you want something only an ASGI server has, such as
HTTP/2 from hypercorn. The pages' WebSocket works where the ASGI server
supports WebSockets; elsewhere pages use Server-Sent Events by themselves.

### Docker

```bash
cd dist && docker build -t myapp . && docker run -p 8000:8000 -e PYWEB_AUTH_SECRET=... myapp
```

The generated `Dockerfile` installs the matching PyWeb version, installs
`requirements.txt` if you put one in `dist/`, and runs `pyweb serve`
with a health check.

### `pyweb deploy`: from app to production in one command

```bash
pyweb deploy fly          # or: docker | compose | k8s | render | railway
```

`pyweb deploy` reads your app (database, jobs, live updates, sign-in,
uploads, migrations), decides how it should run, prints that plan with
the reason for every decision, and writes the files for the target: a
multi-stage, non-root image plus `compose.yaml`, Kubernetes manifests,
`fly.toml`, a Render blueprint or Railway config, with a web process, a
worker for background jobs, and migrations once per deploy. See
[Scaling and deploying](25-scaling.md).

## Production checklist

```bash
pyweb check --production app.pyweb
```

It exits non-zero and says what to fix when:

- `PYWEB_AUTH_SECRET` is missing or shorter than 32 characters;
- `PYWEB_ENV` isn't `production`;
- `PYWEB_COOKIE_SECURE` is off (serve over HTTPS and turn it on);
- `PYWEB_TRUST_PROXY` isn't set (needed behind a reverse proxy);
- `WEB_CONCURRENCY` is above 1 without `PYWEB_REDIS_URL`;
- the app uses `RedisCache(allow_pickle=True)`;
- npm package files don't match `pyweb.lock`;
- the app has Models but no `migrations/`, or migrations not yet applied
  to `DATABASE_URL`;
- the app sends email without `PYWEB_MAIL_URL`, or accepts uploads
  without object storage (`PYWEB_STORAGE=s3://...`);
- `PYWEB_WORKER=0` while the app has jobs (a reminder to run `pyweb worker`).

## Behind a reverse proxy

In production a reverse proxy usually sits in front of PyWeb: it handles
HTTPS and passes requests on. PyWeb then sees every request coming from
the proxy, so tell it to read the visitor's address from the
`X-Forwarded-For` header the proxy adds:

```bash
PYWEB_TRUST_PROXY=1        # one proxy in front (use 2 for a CDN plus a proxy, and so on)
PYWEB_COOKIE_SECURE=1      # the proxy serves HTTPS
```

Only set it when clients can't reach PyWeb directly; otherwise anyone
could send a made-up `X-Forwarded-For`. Without it, rate limits treat
all your visitors as one.

**The easy way:** `pyweb deploy compose --domain example.com` writes a
`compose.yaml` and `Caddyfile` with [Caddy](https://caddyserver.com) in
front. Caddy fetches the HTTPS certificate by itself, the app is only
reachable through it, and both settings above are already set:

```bash
pyweb deploy compose --domain example.com --out .
cp .env.example .env        # fill in PYWEB_AUTH_SECRET (and DB_PASSWORD if it asks)
docker compose up -d --build
```

Point the domain's DNS at the server first. `pyweb deploy k8s`
already sets `PYWEB_TRUST_PROXY=1` (traffic arrives through your
Ingress). Platforms such as Fly.io, Render and Railway also put a proxy
in front of your app: set `PYWEB_TRUST_PROXY=1` there.

With your own proxy, pass the address on. nginx:

```text
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;           # live updates: the pages' WebSocket ...
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_read_timeout 1h;
    proxy_buffering off;              # ... and Server-Sent Events stream immediately
}

# in the http block:
map $http_upgrade $connection_upgrade { default upgrade; '' close; }
```

Caddy (`reverse_proxy` adds `X-Forwarded-For` and passes WebSockets by itself):

```text
example.com {
    reverse_proxy 127.0.0.1:8000
}
```

## Health checks

- `/healthz` answers `200` while the process runs (liveness).
- `/readyz` also checks that every database the app opened with
  `pyweb.db.connect` answers; it returns `503` with the failing check
  when one doesn't (readiness). Point load balancers and Kubernetes
  readiness probes here.

On `SIGTERM` (and ASGI lifespan shutdown), the server ends open live
connections, waits up to 10 seconds for background jobs, then stops.

## Scaling

Servers keep no per-user state: sessions are signed cookies, pages
render per request, and RPC is stateless HTTP. Run as many processes or
containers as you need behind any load balancer; no sticky sessions are
required. Shared state belongs in your database.

With more than one process, set `PYWEB_REDIS_URL` (and
`pip install redis`): rate limits are then shared, so the limit applies
to the whole site rather than per process, and live updates, live
queries and presence in one process reach browsers connected to another. Background jobs are
stored in the database, so every process shares them already. For caches
across processes use `RedisCache`, and for
"sign out everywhere" `auth.use_session_versions(auth.RedisSessionVersions(url))`.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PYWEB_ENV` | `development` | `production` makes `PYWEB_AUTH_SECRET` mandatory for sessions |
| `PYWEB_AUTH_SECRET` | derived (dev only) | root signing secret (sessions, live feeds, live queries) |
| `PYWEB_AUTH_SECRET_PREVIOUS` | unset | old secrets still accepted while you rotate (comma-separated) |
| `PYWEB_COOKIE_SECURE` | off | add `Secure` to cookies (set when behind HTTPS) |
| `PYWEB_CSRF_SECRET` | unset | token CSRF checks for `@auth_required` RPCs |
| `PYWEB_CSP` | see [Security](13-security.md) | override the Content-Security-Policy |
| `PYWEB_TRUST_PROXY` | off | behind a reverse proxy (nginx, Caddy, a load balancer): `1`, or the number of proxies, so rate limits use the visitor's address from `X-Forwarded-For` instead of the proxy's |
| `PYWEB_REDIS_URL` | unset | share rate limits and live updates between server processes |
| `PYWEB_MAX_CONNECTIONS` | `10000` | simultaneous connections per server process (`503` beyond) |
| `PYWEB_THREADS` | `32` | threads per process rendering pages and running server functions |
| `PYWEB_ALLOWED_ORIGINS` | unset | other origins allowed to open the pages' WebSocket (comma-separated); by default only the site itself |
| `WEB_CONCURRENCY` | `1` | worker processes for `pyweb serve` (same as `--workers`) |
| `PYWEB_WORKER` | `1` | `0`: don't run background jobs inside the web server (run `pyweb worker` processes; see [Background jobs](24-jobs.md)) |
| `PYWEB_JOBS` | `db` | where jobs are stored: `db`, `redis` or `memory` |
| `PYWEB_SOCKET_TIMEOUT` | `30` | seconds before a stalled client is dropped |
| `PYWEB_MAX_STREAMS_PER_CLIENT` | `20` | open live connections per client address (`429` beyond) |
| `PYWEB_RENDER_TIMEOUT` | `30` | seconds a page may take to render (`504` beyond) |
| `DATABASE_URL` | — | conventional; read it in your app and pass to `pyweb.db.connect` |

A value that can't be parsed (`PYWEB_MAX_CONNECTIONS=lots`) stops the
server at startup with a message naming the variable.

## Security headers

HTML responses carry a Content-Security-Policy that only allows scripts
from your own origin (the page-state JSON is data, not script). Every
response also carries `X-Content-Type-Options`, `Referrer-Policy`,
`X-Frame-Options`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`
and `Cross-Origin-Resource-Policy`, plus `Strict-Transport-Security`
when `PYWEB_COOKIE_SECURE` is on. See [Security](13-security.md).

## Caching and compression

Files with a content hash in their name (`pyweb build --production`),
npm packages under `static/vendor/<name>@<version>/` and `?v=` URLs are
cached by browsers for a year. Your own files in `static/` (such as
`app.css`) are revalidated on each visit with an `ETag`, so a deploy
shows up at once and an unchanged file costs a `304` with no body.
Text responses (HTML, JavaScript, CSS, JSON, SVG) are gzipped for
browsers that accept it.

## Logs and request ids

`pyweb serve` writes JSON lines to stdout (`{"ts", "level", "msg",
"logger", ...}`). Every response has an `X-Request-Id`; errors in pages
and server functions are logged with it, and the 500 page shows it, so
a user report can be matched to the log line. Incoming W3C
`traceparent` headers are honoured and propagated.
