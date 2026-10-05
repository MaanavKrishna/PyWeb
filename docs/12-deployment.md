# Deployment

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

### Built-in server

```bash
PYWEB_ENV=production PYWEB_AUTH_SECRET=... pyweb serve dist --host 0.0.0.0 --port 8000
```

A threaded, dependency-free HTTP server. It serves hashed assets with
`Cache-Control: immutable`, answers `GET /healthz`, drains in-flight
requests on `SIGTERM`, and applies the security headers below. Put it
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

Page rendering and server functions run in a thread pool, so the event
loop is never blocked. Use this when you want a process manager,
HTTP/1.1 keep-alive tuning or HTTP/2 from your ASGI server.

### Docker

```bash
cd dist && docker build -t myapp . && docker run -p 8000:8000 -e PYWEB_AUTH_SECRET=... myapp
```

The generated `Dockerfile` installs the matching PyWeb version, installs
`requirements.txt` if you put one in `dist/`, and runs `pyweb serve`
with a health check. `pyweb deploy --target docker|compose|k8s` writes
deployment files for an app directory (Kubernetes manifests include
liveness and readiness probes on `/healthz`).

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
- npm package files don't match `pyweb.lock`.

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

**The easy way:** `pyweb deploy --target compose --domain example.com`
writes a `compose.yaml` and `Caddyfile` with [Caddy](https://caddyserver.com)
in front. Caddy fetches the HTTPS certificate by itself, the app is
only reachable through it, and both settings above are already set:

```bash
pyweb deploy --target compose --domain example.com --out deploy
cp -r app.pyweb static deploy/ && cd deploy
PYWEB_AUTH_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))") docker compose up -d
```

Point the domain's DNS at the server first. `pyweb deploy --target k8s`
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
    proxy_buffering off;              # live updates (Server-Sent Events) stream immediately
}
```

Caddy (`reverse_proxy` adds `X-Forwarded-For` by itself):

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
to the whole site rather than per process, and live updates published
in one process reach browsers connected to another. For job queues and
caches across processes use `RedisQueue` and `RedisCache`, and for
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
| `PYWEB_MAX_CONNECTIONS` | `256` | simultaneous connections for `pyweb serve` (`503` beyond) |
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
