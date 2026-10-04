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

## Scaling

Servers keep no per-user state: sessions are signed cookies, pages
render per request, and RPC is stateless HTTP. Run as many processes or
containers as you need behind any load balancer; no sticky sessions are
required. Shared state belongs in your database (and Redis, if you use
`RedisBus`/`RedisQueue`/`RedisCache`).

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PYWEB_ENV` | `development` | `production` makes `PYWEB_AUTH_SECRET` mandatory for sessions |
| `PYWEB_AUTH_SECRET` | derived (dev only) | session signing key |
| `PYWEB_COOKIE_SECURE` | off | add `Secure` to cookies (set when behind HTTPS) |
| `PYWEB_CSRF_SECRET` | unset | token CSRF checks for `@auth_required` RPCs |
| `PYWEB_CSP` | see [Security](13-security.md) | override the Content-Security-Policy |
| `PYWEB_TRUST_PROXY` | off | behind a reverse proxy (nginx, Caddy, a load balancer): `1`, or the number of proxies, so rate limits use the visitor's address from `X-Forwarded-For` instead of the proxy's |
| `DATABASE_URL` | — | conventional; read it in your app and pass to `pyweb.db.connect` |

## Security headers

HTML responses carry a Content-Security-Policy that only allows scripts
from your own origin (the page-state JSON is data, not script), plus
`X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` and
`X-Frame-Options: SAMEORIGIN`.

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
