# Background jobs

Some work shouldn't happen while a visitor waits: sending email, charging
a card, resizing images, calling a slow API, nightly reports. Define it as
a job and queue it; a worker runs it soon after, retries it if it fails,
and nothing is lost if a server restarts.

```pyweb
from pyweb import App, server
from pyweb.models import Model

app = App(database="sqlite:///shop.db")


class Order(Model):
    item: str
    status: str = "new"


@app.job(retries=5, timeout=120)
def ship(order_id: int):
    order = Order.get(order_id)
    order.status = "shipped"
    order.save()


@server
def place(item: str) -> int:
    order = Order.create(item=item)
    ship.enqueue(order.id)            # runs after this function's writes commit
    return order.id


@app.cron("0 3 * * *", tz="Europe/London")
def nightly_report():
    ...


@app.page("/")
def Home():
    <p>Shop</p>
```

## Queuing

- `ship.enqueue(42)` queues a run; `ship.enqueue(42, delay=600)` waits ten
  minutes; `at=datetime(...)` picks a time. It returns a handle with
  `.id`, `.status()` and `.wait()`.
- **Arguments are JSON**: numbers, strings, lists, dicts. Pass ids, not
  objects; the job loads what it needs, so it sees the data as it is when
  it runs.
- **It joins the transaction.** Inside a server function (or another job),
  the job is queued only if the function's database writes commit. If the
  function raises, the order and its shipping job both disappear. This is
  the "outbox" pattern, and it's what makes "save, then email" safe.
- **Duplicates**: `ship.enqueue(42, key="ship:42")` returns the existing job
  while one with that key is queued or running. `@app.job(unique_for=3600)`
  keys jobs by their arguments and keeps the key for an hour after they
  finish, so the same report isn't built twice in that hour.
- Calling `ship(42)` directly just runs it, like any function (handy in
  tests).

## Running

`pyweb dev` and `pyweb serve` run a worker inside every server process,
so jobs run with nothing else to start. For heavy or slow jobs, run
workers separately and keep web processes for pages:

```text
PYWEB_WORKER=0 pyweb serve dist --workers 4      # web: no jobs here
pyweb worker dist --concurrency 8                 # one or more worker processes
pyweb worker dist --queues mail                   # or a worker for one queue
```

Each job runs on its own thread, inside a transaction like a server
function. Workers claim jobs with a lease (`timeout` + 30 seconds): if a
worker crashes or is killed, the job runs again on another worker once the
lease expires. On `SIGTERM` a worker stops taking jobs, gives running ones
`--grace` seconds (25 by default), and hands back the rest so another
worker starts them again (this doesn't count as a failed attempt).

So a job can run more than once (a crash after the work but before it was
recorded as done). Make jobs safe to repeat: check whether the work is
already done (`if order.status == "shipped": return`) or use a key.

## Failures and retries

- An exception is a failed attempt. `retries=5` allows five more after the
  first; the delay before each grows (`backoff=2` seconds, doubling, capped
  at `max_backoff=3600`) with random jitter so retries don't arrive in a
  thundering herd.
- A job over its `timeout` (seconds) is abandoned and counts as failed.
  Python can't stop a thread, so long loops should check
  `pyweb.jobs.current().cancelled.is_set()`.
- After the last attempt the job is **dead**: kept with its error and
  traceback. See and retry them in `/admin` (the `pyweb_jobs` table, for
  admins), or:

```text
pyweb jobs list --state dead
pyweb jobs retry 3f2a9c1e8b7d4a60
pyweb jobs purge --days 7
```

Inside a job, `pyweb.jobs.current()` has `.attempt`, `.last_attempt` and
`.progress(0.5)` (stored on the job's row, so a live query on `JobRecord`
shows progress in a page).

## Schedules

```python
from pyweb.jobs import cron, every


@cron("*/15 9-17 * * mon-fri", tz="America/New_York")
def sync_inventory():
    ...


@every(minutes=5)
def check_payments():
    ...
```

(`@app.cron` and `@app.every` are the same.) Cron fields are minute, hour,
day of month, month and day of week, with `*`, ranges, steps, lists and
names; `@hourly`, `@daily`, `@weekly`, `@monthly`, `@yearly` work too.
Times are in `tz`, and daylight-saving changes are handled: a time that
doesn't exist that day is skipped, and one that happens twice runs once.

Every server process runs the scheduler, and each slot becomes exactly one
job (its key is the schedule and the slot), so there's no leader to elect
and no single point of failure. After downtime, `catchup="latest"` (the
default) runs the most recent missed slot, `"all"` runs every missed slot
(up to 100), and `"none"` skips them.

## Where jobs are stored

| | When | Notes |
|---|---|---|
| The app's database | the app has one (`App(database=...)`) | table `pyweb_jobs`, created by PyWeb (not in your migrations). Postgres and MySQL 8 claim with `FOR UPDATE SKIP LOCKED`, so many workers never block each other. SQLite works with several processes on one machine. |
| Redis | `PYWEB_JOBS=redis` and `PYWEB_REDIS_URL` | when the database shouldn't carry the queue. Claims and outcomes are atomic Lua scripts. Queuing still waits for the transaction to commit, but a crash between the commit and the queuing can lose that one job. |
| Memory | no database | for scripts and tests; jobs are lost on restart. |

## Email

With jobs stored durably and a worker running, `pyweb.mail.send(...)`
queues the email as a job (`pyweb.mail.deliver`, queue `mail`, 8 retries):
a sign-up confirmation is only sent if the sign-up committed, and a mail
server outage delays email instead of failing the request. While
developing (no `PYWEB_MAIL_URL`), email is printed straight away as before.
`PYWEB_MAIL_OUTBOX=0` turns this off and `1` forces it on.

## Housekeeping

A nightly job (`pyweb.cleanup`, at 03:17 UTC) deletes finished jobs older
than `PYWEB_JOBS_KEEP_DAYS` (7), expired sign-in links, and audit log
entries older than `PYWEB_AUDIT_DAYS` (365).

## Settings

| Variable | Default | Purpose |
|---|---|---|
| `PYWEB_JOBS` | `db` (or `memory` without a database) | where jobs are stored: `db`, `redis` or `memory` |
| `PYWEB_WORKER` | `1` | `0`: don't run jobs inside `pyweb serve`/`dev` (run `pyweb worker`) |
| `PYWEB_WORKER_CONCURRENCY` | `4` | jobs at once in a server's built-in worker |
| `PYWEB_JOBS_KEEP_DAYS` | `7` | how long finished jobs are kept |
| `PYWEB_AUDIT_DAYS` | `365` | how long sign-in audit entries are kept |
| `PYWEB_MAIL_OUTBOX` | automatic | `1`/`0`: send email through the job queue or not |

`@task` and `Queue` from 0.4 still work; they run in memory, in the
process that queued them.
