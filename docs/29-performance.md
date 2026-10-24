# Performance

PyWeb is plain Python, and fast enough that the network and your database
take more of a visitor's time than PyWeb does. This page has the numbers,
the tools for finding out where your own app spends its time, and why
PyWeb has no native (Rust or C) core.

## The numbers

Measured on one machine (4 cores) with the examples in this repository.
Run `python -m pyweb.bench --system` to get them for yours.

| What | 0.5 | 0.6 |
|---|---|---|
| Todo page over `pyweb serve`, 32 keep-alive connections | 1,200 req/s | about 3,000 req/s |
| Same, Python 3.15 free-threaded (`python3.15t`) | | about 5,700 req/s |
| Signed-in project page, 23 tasks on SQLite, in-process | 1.66 ms | about 0.92 ms |
| `import pyweb` | 87 ms | 2 ms |
| `pyweb --version` (beyond a bare `python` start) | 120 ms | 16 ms |
| 10,000-row live query: hash and diff | 15 ms | 15 ms, or 6 ms with `[speed]` |

For comparison, the same todo page through uvicorn and PyWeb's ASGI
adapter does about 930 req/s on that machine.

What changed in 0.6:

- **One thread hand-off per request.** Pages used to render on a second
  thread pool so the server could time them out. The time limit now lives
  in the connection handler, and the page renders on the thread that read
  the request.
- **Compiled rendering.** Each page's markup is turned into one Python
  function the first time it's used: text and fixed attributes are
  escaped once, `for` and `if` become real loops and branches, and each
  `{expression}` is ordinary bytecode. The output is byte-for-byte what
  the previous renderer produced. Set `PYWEB_SSR_VERIFY=1` to render every
  page both ways and fail loudly if they ever differ (the test suite does).
- **No system call per id.** Request and trace ids came from
  `os.urandom`, and each call released the interpreter lock, so under load
  request threads queued to get it back. They're labels for logs, not
  secrets, so they now come from a seeded generator. Session ids, tokens
  and keys still use `secrets`.
- **Settings are read once.** `PYWEB_*` variables are parsed again only
  when one of them changes.
- **Lazy start-up.** `import pyweb` loads the parts you use when you use
  them. On Python 3.15, rarely used standard modules (`smtplib`,
  `tarfile`, `urllib.request`) are loaded lazily too.

## Faster JSON (optional)

```bash
pip install "pyweb-stack[speed]"
```

This adds [orjson](https://github.com/ijl/orjson), and PyWeb uses it for
live query updates, WebSocket messages, page state and server function
results. A 10,000-row live query encodes in about 1 ms instead of 10.
Small payloads hardly change, so most apps don't need it.

The JSON is the same apart from two details: very small or very large
floats are written `1e-7` instead of `1e-07`, and `NaN` becomes `null`
(which browsers can parse; they can't parse `NaN`).

## Free-threaded Python

On a free-threaded build (`python3.14t`, `python3.15t`), server functions
and page renders run on several cores inside one process, so one
`pyweb serve` process (the default) uses every core. It says so when it
starts:

```text
serving . on http://0.0.0.0:8000 with 1 worker(s) on a free-threaded Python (each process uses every core) (health: /healthz)
```

Nothing changes in your app. PyWeb's own shared state (caches, the
metrics registry, the job registry, the rate limiter) is locked where
threads meet. Your server functions are just Python, so module-level
state you change from several requests at once needs a lock there too,
the same as with threads on any Python.

## Finding the slow part of your app

### The dev toolbar

`pyweb dev` shows each request's time, its SQL and the spans you added.
It flags the same query running 10 or more times in one request (almost
always a loop that should use `include()`). See
[Observability](26-observability.md).

### `pyweb profile`

```bash
pyweb profile app.pyweb --seconds 20
```

This serves your app on port 8000 and records where the time goes while
you use it in a browser.

- **On Python 3.15** it uses Tachyon, CPython's sampling profiler. Sampling
  reads the process from outside, so the app runs at full speed, and you
  get `profile.html`, an interactive flame graph. `--mode wall` counts
  time spent waiting too (database, network); `--mode gil` shows which
  threads hold the interpreter lock.
- **On older Pythons** a small sampler inside the process writes
  `profile.txt`: the functions that ran most, and the functions most
  time was spent under.

Profile against a copy of your real data. A page that's fast with ten
rows can be slow with ten thousand.

## Fast reloads and tests

The UI kit is compiled once per process, and each `.pyweb` file's Python
code is reused until the file changes. On the saas example that took a
reload in `pyweb dev` from about 255 to 60 ms and creating a
`TestClient` from about 255 to 55 ms; the dev server also notices a save
within a tenth of a second. Compiled output is byte-for-byte what a fresh
compile gives, and a test checks that.

## Benchmarks in CI

`python -m pyweb.bench` reports compile time, shipped JavaScript and
render time per page. `--system` measures the whole stack: requests per
second over real sockets, a signed-in page with a database, start-up
time, live query hashing, and how long loading an app again takes (what
`pyweb dev` does after a save, and what each `TestClient` does).

PyWeb's CI runs `--compare` on every push and pull request. It checks out
the commit before the change next to the new one and runs both five
times, alternating, on the same machine. It fails if the median of a
measure got more than 15% worse, ignoring differences too small to matter
(5 ms of start-up, say). Comparing two commits on one machine avoids
stored numbers from a different, faster or slower, machine.

## Why there's no Rust core

We measured whether moving PyWeb's hot paths to Rust would be worth it.

**What it could save.** On the signed-in page above, about 60% of the time
was PyWeb's own code: rendering, query building, routing, telemetry. Your
template expressions and page functions have to stay Python, so a Rust
renderer would still call back into Python for every `{expression}`. The
best case was about 0.6–0.7 ms saved per request, roughly 2× more
requests per core.

**What Python got instead.** The changes above took the same page from
1.66 ms to 0.92 ms and the todo page from 1,200 to 3,000 requests a
second, most of what Rust would have given.

**What it would mean for you.** A visitor waits for network round trips
(20–100 ms) and database queries (0.5–5 ms each on a networked database).
The 0.3 ms or so Rust would still save is under 1% of that. It would help
only very busy sites save on servers.

**What it would cost.** Prebuilt binaries for every OS, CPU and Python
version (3.10 to 3.15, with and without the lock). The end of a pure
Python install with no compiler needed. A WebAssembly build for the
in-browser playground. A Rust toolchain for every contributor.

So PyWeb stays pure Python. The functions that would move first are kept
behind one small module (`pyweb/_speedups.py`) with fixed names, so a
native version could replace it without changing anything else. We'll
look again if a part with no Python callbacks (say, WebSocket fan-out to
10,000+ connections) ever passes a quarter of request time.
