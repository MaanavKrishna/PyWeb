# Scaling and deploying

A PyWeb app grows through a few stages without code changes. `pyweb
deploy` reads your app, picks the stage that fits the target and the
number of machines you ask for, explains why, and writes the files.

```text
$ pyweb deploy compose --replicas 2
Deploy plan for shop (compose)

  runs:       2 × web + worker
  provisions: postgres, redis
  on deploy:  pyweb db upgrade (once, before the new version starts)

Why:
  - Postgres instead of SQLite: 2 replicas need one shared database (DATABASE_URL replaces the app's sqlite URL; no code change)
  - Redis: 2 server processes share rate limits and live updates and presence through it
  - a separate `pyweb worker` process runs background jobs; web processes only serve pages
  - migrations run once per deploy (`pyweb db upgrade`), under a lock, before new servers start
```

## The stages

1. **One machine, SQLite.** `pyweb serve` handles thousands of open
   connections in one process. Background jobs run inside it. The
   database is a file on a persistent volume. This is a good fit for a
   lot of apps, and `pyweb deploy compose` (or `docker`) sets it up.
2. **Several machines.** They need one shared database (Postgres or
   MySQL) and Redis, so rate limits, live updates and presence reach
   every visitor wherever their connection landed. `DATABASE_URL`
   replaces the URL in `App(database=...)`, so the code you develop on
   SQLite runs on Postgres as it is.
3. **Separate workers.** Web processes only serve pages
   (`PYWEB_WORKER=0`); `pyweb worker` processes run jobs and send email.
   Scale each independently.
4. **Read replicas.** `DATABASE_REPLICA_URL` sends reads to a replica,
   while a request that wrote still reads its own writes from the primary.

## Targets

| Target | What you get |
|---|---|
| `docker` | the image: `Dockerfile` (multi-stage, non-root, health check) and `.dockerignore` |
| `compose` | one machine or several replicas: web, worker, a migrate step, Postgres, Redis, Caddy (HTTPS with `--domain`, load balancing across replicas) |
| `k8s` | Deployments for web and worker, migrations as an init container, a Service, readiness/liveness/startup probes, a disruption budget, an autoscaler and (with `--domain`) an Ingress with long timeouts for live sockets |
| `fly` | `fly.toml` with web and worker processes, a release command for migrations, `/readyz` checks, and the commands that create Postgres and Redis |
| `render` | a `render.yaml` blueprint: web, worker, Key Value (Redis) and Postgres wired together, `PYWEB_AUTH_SECRET` generated |
| `railway` | `railway.json` (and `railway.worker.json`) with the pre-deploy migration and health check |

Options: `--replicas N`, `--processes N` (server processes per machine),
`--db sqlite|postgres|mysql` to override the choice, `--with redis`,
`--domain`, `--region` (Fly), `--name`, `--plan` (print the plan, write
nothing), `--check` (exit 1 if the plan has warnings, for CI) and
`--db-url` to use an existing database.

Every target writes `.env.example` listing what to set; `pyweb check
--production` checks the same things against the real environment.

## Zero-downtime deploys

New and old versions run side by side for a moment during a rolling
deploy, so a schema change must work with both:

1. **Expand.** Add new columns and tables (nullable, or with a default).
   `pyweb db diff` puts additions and removals in separate migrations
   for you.
2. Deploy the code that uses them. The migration runs first, once,
   under a lock.
3. **Contract.** Once no old servers remain, remove what's unused with
   `pyweb db upgrade --contract`.

A rename is split the same way: add the new column, copy the data, and
drop the old one later.

## What happens on shutdown

`SIGTERM` (every platform sends it before stopping a container):

- The server stops accepting connections and finishes requests in
  flight.
- Live sockets are told to reconnect. Browsers do so, to another
  machine.
- Workers stop taking jobs, give running ones up to 25 seconds, and
  hand the rest back so another worker starts them again.

Kubernetes manifests wait 5 seconds before that (`preStop`), so the load
balancer has stopped sending traffic first.
