"""Build the PyWeb docs site into website/dist/ (stdlib only).

Usage:  python website/build.py
Output: website/dist/index.html + *.html + assets/style.css

Single generator, no JS framework, no build deps — builds anywhere
Python exists (CI, laptop, Pages action). Guides mirror docs/*.md.
"""

from __future__ import annotations

import html
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "dist")

CSS = """\
:root{--bg:#0b0f17;--panel:#111827;--line:#1f2a3d;--txt:#e5eaf3;--dim:#9aa7bd;
--acc:#5aa9ff;--acc2:#7ee2a8;--warn:#ffb86b;--code:#0d1420}
*{box-sizing:border-box}body{margin:0;font:16px/1.65 system-ui,-apple-system,
"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--txt)}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1080px;margin:0 auto;padding:0 22px}
header.top{border-bottom:1px solid var(--line);background:#0d1320;position:
sticky;top:0;z-index:5}header.top .wrap{display:flex;gap:18px;align-items:
center;padding:12px 22px}.logo{font-weight:800;font-size:19px;color:#fff}
.logo span{color:var(--acc2)}nav.main{display:flex;gap:14px;flex-wrap:wrap;
font-size:14px}nav.main a{color:var(--dim)}nav.main a:hover{color:#fff}
.hero{padding:72px 0 54px;text-align:center}.hero h1{font-size:52px;margin:0
0 10px;letter-spacing:-1px}.hero h1 span{color:var(--acc2)}.tag{color:var(--dim);
font-size:20px;max-width:720px;margin:0 auto 26px}.cta{display:flex;gap:12px;
justify-content:center;flex-wrap:wrap}.btn{display:inline-block;padding:12px 26px;
border-radius:10px;font-weight:700}.btn.p{background:var(--acc2);color:#06281a}
.btn.s{border:1px solid var(--line);color:var(--txt);background:var(--panel)}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
gap:16px;margin:34px 0}.card{background:var(--panel);border:1px solid var(--line);
border-radius:14px;padding:20px}.card h3{margin:0 0 8px;font-size:17px}
.card p{margin:0;color:var(--dim);font-size:14.5px}pre{background:var(--code);
border:1px solid var(--line);border-radius:12px;padding:18px;overflow:auto;
font-size:14px}code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,
monospace}section.block{margin:44px 0}h2.sec{font-size:28px;margin:0 0 6px}
p.lead{color:var(--dim);max-width:760px}table.spec{width:100%;border-collapse:
collapse;font-size:14.5px}table.spec th,table.spec td{text-align:left;padding:
9px 10px;border-bottom:1px solid var(--line)}table.spec th{color:var(--dim);
font-weight:600}footer{border-top:1px solid var(--line);color:var(--dim);
font-size:13.5px;padding:26px 0 40px;margin-top:60px}.k{display:inline-block;
background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:
1px 8px;font-size:13px;color:var(--acc2)}.warn{background:#231606;border:1px
solid #5a3a17;border-radius:12px;padding:14px 18px;color:var(--warn);
font-size:14.5px}.ok{background:#0a2117;border:1px solid #1d5a38;border-radius:
12px;padding:14px 18px;color:var(--acc2);font-size:14.5px}
@media(max-width:640px){.hero h1{font-size:36px}}
"""

NAV = [("Guide", "guide.html"), ("Reactivity", "reactivity.html"),
       ("RPC & Placement", "rpc.html"), ("Database", "database.html"),
       ("Auth", "auth.html"), ("Realtime & Jobs", "realtime.html"),
       ("Production", "production.html"), ("Deploy", "deploy.html"),
       ("Roadmap", "roadmap.html")]

CODE_COUNTER = """\
from pyweb import App

app = App()

@app.page("/")
def Home():
    count = 0

    def increment():
        count += 1

    <main>
        <h1>Counter</h1>
        <button onclick={increment}>
            Count: {count}
        </button>
    </main>
"""

CODE_TODO = """\
from pyweb import App

app = App()

class Todo(Model):
    title: str
    done: bool = False

@app.page("/")
def Home():
    text = ""
    todos = Todo.all()          # server query, serialized once

    @server
    def add(title: str):        # normal call -> typed RPC
        Todo.create(title=title)

    def submit():
        add(text)               # stub: validation, auth,
                                # CSRF, retries, tracing

    <main>
        <input bind={text} />
        <button onclick={submit}>Add</button>
        for todo in todos:
            <label>{todo.title}</label>
    </main>
"""


def nav_html(active=""):
    links = "".join(
        f'<a href="{href}"'
        + (f' style="color:#fff;font-weight:700"' if href == active else "")
        + f">{label}</a>" for label, href in NAV)
    return ('<header class="top"><div class="wrap">'
            '<a class="logo" href="index.html">Py<span>Web</span></a>'
            f'<nav class="main">{links}</nav></div></header>')


def page(title, active, body):
    return ("<!doctype html><html lang=en><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)} - PyWeb</title>"
            '<link rel=stylesheet href="assets/style.css">'
            f"<body>{nav_html(active)}<div class=wrap>{body}</div>"
            "<footer><div class=wrap>PyWeb - Python from browser to "
            "database. Core open source (MIT). "
            "Self-host anywhere; no mandatory cloud.</div></footer></body></html>")


def code(s):
    return f"<pre><code>{html.escape(s)}</code></pre>"


PAGES = {}


def add(name, title, body):
    PAGES[name] = (title, body)


add("index.html", "Python from browser to database", """
<section class=hero>
<h1>One language. <span>Every layer.</span></h1>
<p class=tag>Write one coherent Python app. PyWeb infers what runs in the
browser vs the server, turns plain variables into fine-grained reactive
state, and generates typed RPC - no manual APIs, no state library, no
bundler config.</p>
<div class=cta>
<a class="btn p" href="guide.html">Get started in 5 minutes</a>
<a class="btn s" href="https://github.com/MaanavKrishna/PyWeb">GitHub</a>
</div></section>

<section class=block>
<h2 class=sec>Plain variables are reactive</h2>
<p class=lead>No Signal(0), no hooks. The compiler sees count read by
markup and mutated by an event, and lowers it to a signal. Updates patch
only the affected text node - no virtual DOM.</p>
""" + code(CODE_COUNTER) + """
</section>

<section class=block>
<h2 class=sec>Server calls feel local</h2>
<p class=lead>Annotate with @server; call it like any function. PyWeb
generates the endpoint, typed stub, serialization, validation, auth
propagation, CSRF, retries, and tracing.</p>
""" + code(CODE_TODO) + """
</section>

<section class=block>
<h2 class=sec>Why PyWeb</h2>
<div class=grid3>
<div class=card><h3>Tiny browser runtime</h3><p>Signals, DOM bindings,
events, RPC transport. Benchmark apps ship ~10 KB each incl. shared
runtime (cached across pages; per-page code is typically under 1 KB);
static pages ship near-zero JS.</p></div>
<div class=card><h3>Secure by default</h3><p>Parameterized SQL only, escaped
templates, signed sessions, secret-leak compiler errors, upload validation.
pyweb check gates CI.</p></div>
<div class=card><h3>Every magic is inspectable</h3><p>pyweb inspect shows
placement decisions and reasons per symbol - browser vs server, and
why.</p></div>
<div class=card><h3>Realtime + offline</h3><p>Live queries push minimal
patches over SSE/WebSocket; offline queue replays with LWW conflicts and
optimistic UI.</p></div>
<div class=card><h3>Real databases</h3><p>Postgres/MySQL/SQLite, pooling,
prepared statements, streaming, retries, versioned migrations with journal
and rollback.</p></div>
<div class=card><h3>Deploy anywhere</h3><p>pyweb build --production emits
hashed assets + manifest; pyweb serve runs threaded with /healthz;
Dockerfile/k8s generated. No lock-in.</p></div>
</div></section>

<section class=block>
<div class=warn><b>Honest scope (v1.0):</b> streaming SSR, WASM browser
target, and native mobile renderers are roadmap - see the
<a href="roadmap.html">roadmap page</a> for what is real, preview, and
future. No vaporware.</div>
</section>
""")

add("guide.html", "Get started", """
<section class=block>
<h2 class=sec>Get started in 5 minutes</h2>
<p class=lead>Install, scaffold, run. The dev server compiles, serves, and
hot-reloads on save.</p>
""" + code("""pip install pyweb

pyweb new shop && cd shop
pyweb dev app.pyweb        # -> http://localhost:8000 (hot reload)

pyweb check app.pyweb      # types + secret-leak gate
pyweb build app.pyweb --out dist --production
pyweb serve dist           # threaded prod server, /healthz""") + """
<h2 class=sec>CLI reference</h2>
<table class=spec>
<tr><th>Command</th><th>Does</th></tr>
<tr><td><span class=k>pyweb new NAME</span></td><td>Scaffold app.pyweb counter</td></tr>
<tr><td><span class=k>pyweb dev FILE [--port] [--no-reload]</span></td><td>Threaded dev server + hot-reload watcher</td></tr>
<tr><td><span class=k>pyweb serve DIR [--app mod:attr]</span></td><td>Serve production dist (never execs source)</td></tr>
<tr><td><span class=k>pyweb build FILE --production</span></td><td>Hashed assets, minified JS, split bundles, importmap, manifest</td></tr>
<tr><td><span class=k>pyweb check FILE</span></td><td>Compile + security findings; fails CI on secret-leak</td></tr>
<tr><td><span class=k>pyweb inspect FILE [--security]</span></td><td>Placement decisions with reasons, RPC list</td></tr>
<tr><td><span class=k>pyweb test [PATH]</span></td><td>Run pytest suite</td></tr>
<tr><td><span class=k>pyweb fmt / lint</span></td><td>Ruff when installed, stdlib fallback otherwise</td></tr>
<tr><td><span class=k>pyweb db migrate|status|new</span></td><td>Versioned migrations with journal + rollback</td></tr>
<tr><td><span class=k>pyweb npm FILE.d.ts</span></td><td>Typed Python stubs from TypeScript declarations</td></tr>
<tr><td><span class=k>pyweb deploy --target docker|compose|k8s</span></td><td>Dockerfile (healthcheck), compose, k8s (probes)</td></tr>
</table></section>
""")

add("reactivity.html", "Reactivity without VDOM", """
<section class=block>
<h2 class=sec>Normal variables become signals</h2>
<p class=lead>Stages: parse markup, compute_reactive finds locals read by
UI and mutated by handlers, each becomes a signal, computed values form a
dependency graph, JS updates only bound nodes.</p>
""" + code("""price = 100
quantity = 2
total = price * quantity   # compiler: computed(price, quantity)

<p>{total}</p>             # one text-node binding; a price change
                           # patches that node only""") + """
<p class=lead>Explicit primitives exist for advanced use -
Signal / Computed / Resource / Effect - but basic apps never need them.
Time-travel: the event log records every transition for replay.</p></section>
""")

add("rpc.html", "RPC and placement", """
<section class=block>
<h2 class=sec>One program, two (or more) machines</h2>
<p class=lead>The placement pass builds symbol, effect, data, and security
graphs, then assigns each function to browser / server / edge / worker /
shared. Secrets, DB drivers, and privileged imports pin their module to
the server - the compiler errors instead of leaking.</p>
""" + code("""@server
def create_user(name: str, email: Email) -> User:
    return User.create(name=name, email=email)

def submit():                         # browser code
    user = create_user(name, email)   # typed stub: auth, CSRF,
                                      # retries, timeout, trace""") + """
<p class=lead>Overrides (@browser @server @edge @worker @shared) are escape
hatches, not daily syntax. pyweb inspect prints every decision with its
reason.</p></section>
""")

add("database.html", "Database", """
<section class=block>
<h2 class=sec>Postgres-first, honest everywhere</h2>
<p class=lead>Parameterized queries only (values never touch SQL text),
pooling, prepared statements, streaming cursors, transient-error retries,
pagination. connect(DATABASE_URL) opens sqlite / postgres / mysql.</p>
""" + code("""class Product(Model):
    name: str
    price: Decimal
    stock: int

cheap = Product.where(stock__gt=0, price__lt=1000).paginate(page=2)

# migrations are versioned + journaled
pyweb db new add_stock_index
pyweb db migrate --database $DATABASE_URL
pyweb db status""") + """
</section>
""")

add("auth.html", "Auth and security", """
<section class=block>
<h2 class=sec>Auth is compiler-aware</h2>
<p class=lead>Signed sessions (HttpOnly, SameSite=Lax), PBKDF2 passwords,
RBAC policies, session rotation, magic links, TOTP, OAuth/OIDC clients,
and WebAuthn verification. Security boundaries feed placement: auth-gated
code stays server-side.</p>
""" + code("""@app.page("/dashboard")
@auth.required
def dashboard(): ...

@permission("admin")
def delete_user(uid: int): ...

pyweb check app.pyweb   # secret-leak + vulnerability scan in CI""") + """
<div class=ok>XSS (escaped templates) / SQLi (parameterized only) / CSRF
(tokens + SameSite) / uploads (type/size validation + safe paths) / secret
leak (compile error, not warning).</div></section>
""")

add("realtime.html", "Realtime, jobs, offline", """
<section class=block>
<h2 class=sec>Live data without plumbing</h2>
<p class=lead>Live queries track row dependencies and push minimal patches
(SSE with Last-Event-ID replay, WebSocket where open, polling fallback).
Background @task jobs persist on Redis with retries and progress;
@cache(minutes=5) + SWR covers reads.</p>
""" + code("""msgs = live(Message.where(room=current_room))

@task
def generate_report(user: User): ...
job = generate_report(user)
if job.pending: <Spinner />""") + """
<p class=lead>Offline: queued mutations replay on reconnect with
last-writer-wins + tombstones and conflict reports; optimistic UI rolls
back on failure.</p></section>
""")

add("production.html", "Production operation", """
<section class=block>
<h2 class=sec>Operate it like you mean it</h2>
<table class=spec>
<tr><th>Concern</th><th>PyWeb answer</th></tr>
<tr><td>Serving</td><td>pyweb serve dist: threaded, immutable asset caching,
/healthz (+/readyz)</td></tr>
<tr><td>Observability</td><td>W3C traces button to RPC to DB to worker to
DOM, error taxonomy with codes, structured logs, metrics, event log</td></tr>
<tr><td>Caching</td><td>Memory + Redis, tags, SWR, invalidation that
works</td></tr>
<tr><td>Testing</td><td>Virtual-browser client: full-stack tests without a
browser; real-browser hooks when needed</td></tr>
<tr><td>Budgets</td><td>--budget static/app.js=30kb fails builds that
bloat</td></tr>
<tr><td>npm</td><td>package("chart.js@4.4.0") becomes a pinned importmap in
the SSR shell + manifest</td></tr>
</table></section>
""")

add("deploy.html", "Deploy anywhere", """
<section class=block>
<h2 class=sec>No mandatory cloud</h2>
<p class=lead>Standard artifacts: Postgres stays Postgres, containers stay
containers. One command scaffolds what your platform needs.</p>
""" + code("""pyweb deploy --target docker               # build+serve+HEALTHCHECK
pyweb deploy --target compose --db-url $DATABASE_URL
pyweb deploy --target k8s --image registry/shop:v1  # liveness+readiness

docker build -t shop . && docker run -p 8000:8000 shop
# /healthz answers; probes already wired""") + """
</section>
""")

add("roadmap.html", "Roadmap: what is real", """
<section class=block>
<h2 class=sec>Honest roadmap</h2>
<table class=spec>
<tr><th>Status</th><th>Area</th></tr>
<tr><td><span class=k>real</span></td><td>Reactivity, RPC, placement, SSR,
routing, Postgres/MySQL/SQLite + journaled migrations, auth/RBAC, realtime
bus, jobs, cache, offline sync core, hashed builds, serve + health,
importmap, inspect, tests</td></tr>
<tr><td><span class=k>preview</span></td><td>Mobile/desktop targets (AST
pruning works, native emitters partial), Tailwind bridge, plugin SDK
surface</td></tr>
<tr><td><span class=k>future</span></td><td>Streaming SSR, WASM browser
target, multi-node CRDT sync, managed cloud offering</td></tr>
</table>
<div class=warn>Rule: uncertain code stays on the server. The compiler
optimizes only when it can prove safety - correctness first, always.</div>
</section>
""")


def build():
    os.makedirs(os.path.join(OUT, "assets"), exist_ok=True)
    with open(os.path.join(OUT, "assets", "style.css"), "w") as fh:
        fh.write(CSS)
    for name, (title, body) in PAGES.items():
        with open(os.path.join(OUT, name), "w") as fh:
            fh.write(page(title, name, body))
    print(f"built {len(PAGES)} pages -> {OUT}/")


if __name__ == "__main__":
    build()
