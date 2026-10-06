"""`pyweb` CLI: new / dev / build / test / check / inspect / deploy."""

from __future__ import annotations

import argparse
import os
import sys


def _load(path):
    with open(path) as fh:
        return fh.read()


def cmd_inspect(args):
    from pyweb.compiler import compile_source
    src = _load(args.file)
    out = compile_source(src, filename=args.file)
    print(out["ir_text"])
    print("---")
    for name, lay in out.get("layouts", {}).items():
        info = lay["info"]
        print(f"layout {name} prefix={lay['prefix']} state={[n for n in info.order if n in info.sent]}")
        for symbol, (loc, reason) in info.reasons.items():
            print(f"  {loc:14s} {symbol}  # {reason}")
    for name, page in out["pages"].items():
        where = f"error={page['error_status']}" if page.get("error_status") else f"route={page['route']}"
        print(f"page {name} {where} signals={page['signals']} computeds={list(page['computeds'])}"
              + (f" layouts={page['layouts']}" if page.get("layouts") else ""))
        placement = page.get("placement") or {}
        for symbol, decision in placement.items():
            loc, reason = decision if isinstance(decision, tuple) else (decision, "")
            print(f"  {loc:14s} {symbol}" + (f"  # {reason}" if reason else ""))
    for spec in out["rpc"]:
        print(f"rpc {spec['name']}({', '.join(a['name']+': '+a['type'] for a in spec['args'])}) -> {spec['returns']} [{spec['location']}] line {spec['line']}")
    if getattr(args, "security", False):
        from pyweb.security import check_source
        for finding in check_source(src, args.file):
            print(f"{finding['kind']} {args.file}:{finding['line']}: {finding['message']}")


def cmd_check(args):
    from pyweb.compiler import compile_source
    from pyweb.security import check_source
    src = _load(args.file)
    findings = check_source(src, args.file)
    try:
        out = compile_source(src, filename=args.file)
        n_sig = sum(len(p["signals"]) for p in out["pages"].values())
        print(f"ok: {len(out['pages'])} page(s), {n_sig} signal(s), {len(out['rpc'])} rpc(s)")
    except Exception as exc:  # noqa: BLE001
        findings.append({"kind": "compile-error", "line": 0, "message": str(exc)})
    for f in findings:
        print(f"{f['kind']} {args.file}:{f['line']}: {f['message']}")
    problems = production_problems(src, os.path.dirname(os.path.abspath(args.file))) \
        if getattr(args, "production", False) else []
    for p in problems:
        print(f"production: {p}")
    if getattr(args, "production", False) and not problems:
        print("production: ready")
    if problems or any(f["kind"] in ("secret-leak", "compile-error") for f in findings):
        raise SystemExit(1)


def production_problems(source="", app_dir="."):
    """What would make this app unsafe or unreliable in production, from the environment and the app."""
    from pyweb import config
    out = []
    try:
        s = config.settings()
    except ValueError as exc:
        return [str(exc)]
    if not s.auth_secret:
        out.append("PYWEB_AUTH_SECRET isn't set: sessions would use a development key "
                   "(generate one: python -c \"import secrets; print(secrets.token_hex(32))\")")
    elif len(s.auth_secret) < 32:
        out.append("PYWEB_AUTH_SECRET is shorter than 32 characters; use a long random value")
    if not s.production:
        out.append("PYWEB_ENV isn't 'production' (it makes a missing secret an error instead of a fallback)")
    if not s.cookie_secure:
        out.append("PYWEB_COOKIE_SECURE isn't on: cookies would also travel over plain HTTP "
                   "(set it when you serve over HTTPS, which you should)")
    if not s.trust_proxy:
        out.append("PYWEB_TRUST_PROXY isn't set: behind nginx, Caddy or a load balancer every visitor "
                   "looks like the proxy to rate limits (set it to 1, or the number of proxies; "
                   "ignore this if clients connect directly)")
    try:
        workers = int(os.environ.get("WEB_CONCURRENCY", "1") or 1)
    except ValueError:
        workers = 1
    if workers > 1 and not s.redis_url:
        out.append(f"WEB_CONCURRENCY={workers} without PYWEB_REDIS_URL: each worker would keep its own rate "
                   "limits and live updates (connect Redis to share them)")
    if "use_auth(" in (source or "") and not os.environ.get("PYWEB_ORIGIN") and "origin=" not in source:
        out.append("app.use_auth() without PYWEB_ORIGIN: password-reset and sign-in emails need the site's "
                   "public address (set PYWEB_ORIGIN=https://example.com)")
    if "allow_pickle=True" in (source or ""):
        out.append("RedisCache(allow_pickle=True): anyone who can write to Redis could run code; store JSON")
    out += _topology_problems(app_dir)
    if os.path.isfile(os.path.join(app_dir, "pyweb.lock")):
        from pyweb import packages
        for path, why in packages.verify(app_dir)[:5]:
            out.append(f"{path}: {why} (run pyweb add to reinstall)")
    return out


def _topology_problems(app_dir):
    """What the app uses that production must provide (from the same reading `pyweb deploy` does)."""
    from pyweb.deploy import read_app
    app_file = os.path.join(app_dir, "app.pyweb")
    if not os.path.isfile(app_file):
        return []
    facts = read_app(app_file)
    out = []
    env = os.environ
    if facts.models and not facts.migrations:
        out.append("the app has Models but no migrations/: production doesn't create tables by itself "
                   "(run `pyweb db diff --name initial` and commit migrations/)")
    if facts.migrations and (env.get("DATABASE_URL") or facts.database in ("sqlite", "postgres", "mysql")):
        try:
            from pyweb.db import connect
            from pyweb.db import migrate as _mig
            url = env.get("DATABASE_URL")
            if url:
                waiting = _mig.pending(connect(url), os.path.join(app_dir, "migrations"))
                if waiting:
                    out.append(f"{len(waiting)} migration(s) not applied yet: "
                               f"{', '.join(m.label for m in waiting[:5])} (pyweb db upgrade)")
        except Exception as exc:  # noqa: BLE001 - can't reach the database from here: say so, don't fail
            out.append(f"couldn't check migrations against DATABASE_URL ({type(exc).__name__}: {exc})")
    if facts.mail and not env.get("PYWEB_MAIL_URL"):
        out.append("the app sends email (sign-in links, resets) but PYWEB_MAIL_URL isn't set")
    if facts.uploads and not env.get("PYWEB_STORAGE", "").startswith("s3"):
        out.append("the app accepts uploads but PYWEB_STORAGE isn't object storage (s3://...): files on a "
                   "container's disk vanish on redeploy (ignore this with a persistent volume)")
    if facts.jobs and env.get("PYWEB_WORKER", "1").strip().lower() in ("0", "false", "no", "off"):
        out.append("PYWEB_WORKER=0: make sure `pyweb worker` processes run, or queued jobs will wait forever "
                   "(this is a reminder; ignore it if they do)")
    return out


def cmd_build(args):
    from pyweb.compiler import compile_source
    from pyweb.build import build as _build
    src = _load(args.file)
    out = compile_source(src, filename=args.file)
    production = getattr(args, "production", False)
    try:
        manifest = _build(out, args.out, source=src, production=production,
                          app_dir=os.path.dirname(os.path.abspath(args.file)))
    except RuntimeError as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    mode = " (production)" if production else ""
    print(f"built {len(manifest['pages'])} page(s) + {len(out['rpc'])} rpc(s) -> {args.out}/{mode}")
    from pathlib import Path as _Path
    from pyweb.observability import Timer as _Timer
    with _Timer() as _t:
        _total = sum(p.stat().st_size for p in _Path(args.out).rglob("*") if p.is_file())
    _breaches = []
    for _spec in getattr(args, "budget", []) or []:
        _name, _, _limit = _spec.partition("=")
        _limit_b = _parse_budget(_limit) if _limit else _parse_budget(_name)
        _target = _Path(args.out) / _name if _limit else None
        _size = _target.stat().st_size if _target and _target.exists() else _total
        _label = _name if _limit else "total"
        if _size > _limit_b:
            _breaches.append(f"budget breach: {_label} is {_size}B > {_limit_b}B")
    for _b in _breaches:
        print(f"build: {_b}", file=sys.stderr)
    if _breaches:
        raise SystemExit(2)


DEV_RELOAD_JS = """<script>(()=>{let v=null;async function t(){try{const r=await fetch('/__pyweb/dev/version');const n=await r.text();if(v!==null&&n!==v)location.reload();v=n}catch{}setTimeout(t,600)}t()})()</script>"""


def _error_overlay(exc, path):
    """HTML page shown in the browser while the app has a compile error."""
    import html as _h
    lineno = getattr(exc, "lineno", None)
    snippet = ""
    try:
        lines = _load(path).splitlines()
        if lineno:
            lo, hi = max(0, lineno - 4), min(len(lines), lineno + 3)
            rows = []
            for i in range(lo, hi):
                mark = "&gt;" if i + 1 == lineno else "&nbsp;"
                rows.append(f"{mark} {i + 1:4d} | {_h.escape(lines[i])}")
            snippet = "<pre class=code>" + "\n".join(rows) + "</pre>"
    except OSError:
        pass
    return ("<!doctype html><meta charset=utf-8><title>PyWeb error</title>"
            "<style>body{font:15px/1.5 system-ui;margin:0;background:#1b1b1f;color:#eee}"
            "main{max-width:880px;margin:8vh auto;padding:0 24px}h1{color:#ff6b6b;font-size:20px}"
            "pre{background:#0f0f12;padding:16px;border-radius:8px;overflow:auto;font-size:13px}"
            ".code{border-left:3px solid #ff6b6b}</style><main>"
            f"<h1>{_h.escape(type(exc).__name__)}</h1><pre>{_h.escape(str(exc))}</pre>{snippet}"
            "<p>Fix the file and save: this page reloads automatically.</p></main>" + DEV_RELOAD_JS)


class _DevState:
    def __init__(self, path):
        self.path = path
        self.site = None
        self.error = None
        self.version = 0


def _dev_load(state, Site):
    try:
        if state.site is None:
            state.site = Site(state.path, debug=True)
        else:
            state.site.reload()
        state.error = None
    except Exception as exc:  # noqa: BLE001 - shown in the browser overlay
        state.error = exc
    state.version += 1


class _DevSite:
    """What the server serves while developing: the current build of the app, the error
    overlay while it doesn't compile, and a script that reloads pages after a change."""

    def __init__(self, state):
        self.state = state

    @property
    def max_body(self):
        site = self.state.site
        return site.max_body if site is not None else 1_048_576

    def respond(self, method, path, headers, body=b"", client=None):
        state = self.state
        if path == "/__pyweb/dev/version":
            return 200, [("Content-Type", "text/plain"), ("Cache-Control", "no-store")], str(state.version).encode()
        if state.error is not None or state.site is None:
            return 500, [("Content-Type", "text/html; charset=utf-8")], _error_overlay(state.error, state.path).encode()
        status, hdrs, raw = state.site.respond(method, path, headers, body, client=client)
        ctype = next((v for k, v in hdrs if k.lower() == "content-type"), "")
        if ctype.startswith("text/html") and isinstance(raw, bytes) and b"</body>" in raw:
            raw = raw.replace(b"</body>", DEV_RELOAD_JS.encode() + b"</body>", 1)
            hdrs = [(k, v) for k, v in hdrs if k.lower() != "content-length"]
        return status, hdrs, raw

    def websocket(self, path, headers, client=None):
        if self.state.site is None:
            return 503, "the app doesn't compile"
        return self.state.site.websocket(path, headers, client)


def cmd_dev(args):
    """Development server: compile on save, live reload, error overlay."""
    from pyweb.hosting import Site
    from pyweb.net import server as _net

    state = _DevState(args.file)
    _dev_load(state, Site)
    if state.error is not None:
        print(f"error: {state.error}", file=sys.stderr)
    host = getattr(args, "host", "127.0.0.1") or "127.0.0.1"

    def ready(port):
        print(f"PyWeb dev server: http://{host}:{port}/  ({args.file})", flush=True)
        if not getattr(args, "no_reload", False):
            _watch_and_reload(state, Site)
        from pyweb import jobs
        jobs.start_embedded()

    _net.run(lambda: _DevSite(state), host=host, port=args.port, ready=ready)


def _watch_and_reload(state, Site):
    """Poll the app file and sibling ``.py``/``static`` files; reload on change."""
    import threading
    import time

    app_dir = os.path.dirname(os.path.abspath(state.path))

    skip = {"__pycache__", "node_modules", "dist", "build", "venv", "env", "site-packages", "vendor"}

    def snapshot():
        stamps = {}
        for root, dirs, files in os.walk(app_dir):
            # Virtualenvs (any folder with a pyvenv.cfg) hold thousands of files that never matter here;
            # npm packages are covered by pyweb.lock, which changes whenever they do.
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in skip
                       and not os.path.exists(os.path.join(root, d, "pyvenv.cfg"))]
            for fn in files:
                if fn.endswith((".pyweb", ".py", ".css", ".js")) or fn == "pyweb.lock":
                    p = os.path.join(root, fn)
                    try:
                        stamps[p] = os.path.getmtime(p)
                    except OSError:
                        pass
        return stamps

    def purge_local_modules():
        for name, mod in list(sys.modules.items()):
            f = getattr(mod, "__file__", None) or ""
            if f and os.path.abspath(f).startswith(app_dir + os.sep) and f.endswith(".py"):
                del sys.modules[name]

    def poll():
        last = snapshot()
        while True:
            time.sleep(0.4)
            now = snapshot()
            if now != last:
                last = now
                purge_local_modules()
                _dev_load(state, Site)
                if state.error is not None:
                    print(f"error: {state.error}", flush=True)
                else:
                    print(f"reloaded {state.path}", flush=True)

    threading.Thread(target=poll, daemon=True, name="pyweb-reload").start()


def cmd_serve(args):
    from pyweb import observability as _obs
    logger = _obs.Logger("serve")
    if args.app:                                     # legacy: RPC implementations from a factory
        from pyweb import serve as _serve
        httpd = _serve.serve(args.dir, host=args.host, port=args.port, app_factory=args.app, logger=logger)
        _serve.install_shutdown_handlers(httpd, logger=logger)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
        return
    from pyweb import config
    from pyweb.net import server as _net
    config.startup()
    if getattr(args, "migrate", False) or os.environ.get("PYWEB_MIGRATE_ON_START", "").lower() in ("1", "true", "yes"):
        import subprocess
        app_file = os.path.join(args.dir, "app.pyweb")
        done = subprocess.run([sys.executable, "-m", "pyweb.cli", "db", "upgrade", "--app", app_file], check=False)
        if done.returncode != 0:
            raise SystemExit("migrations failed; not serving")
    workers = args.workers or int(os.environ.get("WEB_CONCURRENCY", "1") or 1)

    def make_site():
        from pyweb.hosting import Site
        return Site(args.dir, max_body=1_048_576)

    def ready(port):
        print(f"serving {args.dir} on http://{args.host}:{port} with {workers} worker(s) (health: /healthz)",
              flush=True)

    _net.run(make_site, host=args.host, port=args.port, workers=workers, logger=logger, ready=ready)


def cmd_test(args):
    import subprocess
    import sys as _sys
    cmd = [_sys.executable, "-m", "pytest", "tests/", "-q"]
    if getattr(args, "path", None):
        cmd = [_sys.executable, "-m", "pytest", args.path, "-q"]
    raise SystemExit(subprocess.call(cmd))


def cmd_fmt(args):
    """Format: ruff if available, else a stdlib py_compile sanity pass."""
    import subprocess
    import sys as _sys
    targets = [args.path] if getattr(args, "path", None) else ["pyweb", "tests"]
    try:
        raise SystemExit(subprocess.call(
            [_sys.executable, "-m", "ruff", "format", *targets]))
    except FileNotFoundError:
        pass
    import py_compile
    count, failed = 0, []
    for target in targets:
        for root, _dirs, files in os.walk(target):
            if "__pycache__" in root:
                continue
            for fn in files:
                if fn.endswith(".py"):
                    p = os.path.join(root, fn)
                    try:
                        py_compile.compile(p, doraise=True)
                        count += 1
                    except py_compile.PyCompileError:
                        failed.append(p)
    print(f"fmt: {count} file(s) compile-clean"
          + (f", FAILED: {failed}" if failed else " (ruff not installed; "
             "install ruff for real formatting)"))
    raise SystemExit(1 if failed else 0)


def cmd_lint(args):
    import subprocess
    import sys as _sys
    targets = [args.path] if getattr(args, "path", None) else ["pyweb", "tests"]
    try:
        raise SystemExit(subprocess.call(
            [_sys.executable, "-m", "ruff", "check", *targets]))
    except FileNotFoundError:
        pass
    import ast as _ast
    issues = []
    for target in targets:
        for root, _dirs, files in os.walk(target):
            if "__pycache__" in root:
                continue
            for fn in files:
                if fn.endswith(".py"):
                    p = os.path.join(root, fn)
                    with open(p) as fh:
                        src = fh.read()
                    try:
                        _ast.parse(src)
                    except SyntaxError as exc:
                        issues.append(f"{p}:{exc.lineno}: syntax {exc.msg}")
                    for i, line in enumerate(src.splitlines(), 1):
                        if len(line) > 120:
                            issues.append(f"{p}:{i}: line too long ({len(line)})")
    for issue in issues[:50]:
        print(issue)
    print(f"lint: {len(issues)} issue(s) (ruff not installed; "
          "install ruff for full lint)")
    raise SystemExit(1 if issues else 0)


def _db_context(args, *, need_models=False):
    """``(db, migrations_dir, models)`` for ``pyweb db ...``: the app's Models and database."""
    from pyweb import models as M
    from pyweb.db import connect
    app_path = getattr(args, "app", None) or ("app.pyweb" if os.path.isfile("app.pyweb") else None)
    models = []
    if app_path:
        if not os.path.isfile(app_path):
            raise SystemExit(f"error: no app at {app_path}")
        from pyweb.app_loader import LoadedApp
        previous = os.environ.get("PYWEB_NO_AUTO_MIGRATE")
        os.environ["PYWEB_NO_AUTO_MIGRATE"] = "1"          # `db` commands decide what to apply
        try:
            loaded = LoadedApp(app_path)
        finally:
            if previous is None:
                os.environ.pop("PYWEB_NO_AUTO_MIGRATE", None)
            else:
                os.environ["PYWEB_NO_AUTO_MIGRATE"] = previous
        models = loaded.models()
    elif need_models:
        raise SystemExit("error: pass --app app.pyweb (the app whose Models to compare)")
    url = args.database or os.environ.get("DATABASE_URL")
    db = connect(url) if url else (M.database() if app_path else None)
    if db is None:
        db = connect(":memory:")
    migrations = args.migrations
    if migrations is None:
        migrations = os.path.join(os.path.dirname(os.path.abspath(app_path)), "migrations") if app_path else "migrations"
    return db, migrations, models


def _load_app_for_jobs(target):
    """Load the app (a ``.pyweb`` file or a built ``dist/``) so its database and jobs are set up."""
    from pyweb import config
    from pyweb.app_loader import LoadedApp
    config.startup()
    path = os.path.join(target, "app.pyweb") if os.path.isdir(target) else target
    if not os.path.isfile(path):
        raise SystemExit(f"no app at {target} (pass app.pyweb or a built dist/)")
    return LoadedApp(path)


def cmd_worker(args):
    """Run jobs (and schedules) until SIGTERM/SIGINT; then finish or hand back running jobs."""
    import signal
    import threading
    from pyweb import jobs
    _load_app_for_jobs(args.app)
    queues = [q.strip() for q in (args.queues or "").split(",") if q.strip()] or None
    worker = jobs.Worker(queues=queues, concurrency=args.concurrency, schedule=not args.no_schedule)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, lambda *_: stop.set())
        except ValueError:
            pass
    worker.start()
    print(f"worker {worker.name}: queues {', '.join(worker.queue_names())}, concurrency {worker.concurrency}"
          f", store {type(jobs.backend()).__name__}", flush=True)
    while not stop.wait(0.5):
        pass
    left = worker.stop(args.grace)
    print(f"worker stopped ({left} job(s) handed back)", flush=True)


def cmd_jobs(args):
    import datetime as _dt
    from pyweb import jobs
    _load_app_for_jobs(args.app)
    store = jobs.backend()
    if args.jobs_action == "list":
        rows = store.list(state=args.state, limit=args.limit, name=args.name)
        for r in rows:
            when = _dt.datetime.fromtimestamp(r["created_at"] or 0).strftime("%Y-%m-%d %H:%M:%S")
            err = (r["last_error"] or "").splitlines()[0][:60] if r["last_error"] else ""
            print(f"{r['id']}  {r['state']:<7} {r['attempts']}/{r['max_attempts']}  {when}  {r['name']}  {err}")
        if not rows:
            print("no jobs")
    elif args.jobs_action == "retry":
        if not args.ids:
            raise SystemExit("pyweb jobs retry ID [ID ...]")
        for job_id in args.ids:
            print(f"{job_id}: " + ("queued again" if store.retry(job_id) else "not found, or not failed/dead"))
    elif args.jobs_action == "purge":
        import time as _time
        print(f"removed {store.purge(_time.time() - args.days * 86400)} finished job(s)")
    elif args.jobs_action == "run":
        n = jobs.Worker(concurrency=4, schedule=False).drain(timeout=args.timeout)
        print(f"ran {n} job(s)")


def cmd_db(args):
    from pyweb.db import migrate as _migrate
    action = {"migrate": "upgrade", "rollback": "downgrade"}.get(args.db_action, args.db_action)
    try:
        if action == "new":
            migrations = args.migrations or "migrations"
            path = (_migrate.new_python_migration(migrations, args.name) if args.python
                    else _migrate.new_migration(migrations, args.name))
            print(f"created {path}")
            return
        db, migrations, models = _db_context(args, need_models=action == "diff")
        if action == "upgrade":
            if args.db_action == "migrate" and not args.contract:
                applied = _migrate.migrate(db, migrations)
            else:
                applied = _migrate.upgrade(db, migrations, target=args.target, contract=args.contract)
            print(f"applied {len(applied)} migration(s): "
                  + (", ".join(applied) if applied else "already up to date"))
            waiting = [m for m in _migrate.pending(db, migrations) if m.contract]
            if waiting:
                print("waiting: " + ", ".join(m.label for m in waiting)
                      + " (contract steps: run `pyweb db upgrade --contract` once every server runs the new code)")
        elif action == "downgrade":
            rolled = _migrate.downgrade(db, migrations, steps=args.steps, to=args.to)
            print("rolled back: " + (", ".join(rolled) if rolled else "nothing to roll back"))
        elif action == "status":
            print(_migrate.report(db, migrations, models or None))
        elif action == "diff":
            renames = dict(r.split("=", 1) for r in args.rename)
            table_renames = dict(r.split("=", 1) for r in args.rename_table)
            paths, plan = _migrate.make_migration(db, models, migrations, args.name if args.name != "migration" else None,
                                                  renames=renames, table_renames=table_renames,
                                                  allow_destructive=args.allow_destructive)
            for note in plan.notes:
                print(f"note: {note}")
            if not paths:
                print("no changes: the database matches the Models")
            for path in paths:
                print(f"wrote {path}")
            if len(paths) > 1 or (paths and plan.contract and not args.allow_destructive):
                print("the *_contract migration removes things: deploy first, then run "
                      "`pyweb db upgrade --contract`")
        elif action == "adopt":
            print(f"wrote {_migrate.adopt(db, migrations)} (recorded as applied; nothing changed)")
        elif action == "squash":
            print(f"wrote {_migrate.squash(db, migrations)}")
        elif action == "seed":
            from pyweb.db import seeds
            base = os.path.dirname(os.path.abspath(args.app)) if getattr(args, "app", None) else os.getcwd()
            ran = seeds.run(args.seeds or os.path.join(base, "seeds.py"), db=db)
            print("seeded: " + (", ".join(ran) if ran else "nothing to run"))
        else:
            raise SystemExit(f"unknown db action {action!r}")
    except (ValueError, RuntimeError, FileNotFoundError, TimeoutError) as exc:
        raise SystemExit(f"error: {exc}")


def cmd_deploy(args):
    """Read the app, decide the production topology, and write files for the target."""
    from pyweb import deploy as D
    target = (args.target_pos or args.target or "docker").lower()
    if target == "docker" and args.compose:
        target = "compose"
    if target not in D.TARGETS:
        raise SystemExit(f"unknown deploy target {target!r} (one of: {', '.join(D.TARGETS)})")
    facts = D.read_app(args.file)
    with_services = [s for s in (args.with_services or "").split(",") if s.strip()]
    plan = D.make_plan(target, facts, replicas=args.replicas, db=args.db, domain=args.domain or "",
                       region=args.region or "", with_services=with_services, port=args.port,
                       image=args.image or "", name=args.name or (None if args.app == "pyweb" else args.app),
                       processes=args.processes, database_url=args.db_url or None)
    print(D.describe(plan))
    if args.plan:
        return
    if args.check and plan.warnings:
        raise SystemExit(1)
    outdir = args.out
    os.makedirs(outdir, exist_ok=True)
    files = D.files_for(plan)
    for name, body in files.items():
        with open(os.path.join(outdir, name), "w") as fh:
            fh.write(body)
    print(f"\nwrote {outdir}/: {', '.join(files)}")
    steps = D.next_steps(plan)
    if steps:
        print("\nNext:" + "".join(f"\n  {s}" for s in steps))
        if outdir not in (".", ""):
            print(f"  (the build context is your app folder: copy these files next to {facts.app_file}, "
                  f"or run from there with --out .)")


def cmd_dts(args):
    from pyweb.dts import dts_main
    raise SystemExit(dts_main([args.dts] + (["-o", args.out] if args.out else [])))


def _app_dir(args):
    app = getattr(args, "app", None) or "app.pyweb"
    return os.path.dirname(os.path.abspath(app))


def cmd_add(args):
    from pyweb import packages
    lock = packages.install(_app_dir(args), args.packages)
    for name, p in lock["packages"].items():
        kind = "" if p["direct"] else "  (dependency)"
        print(f"  {name}@{p['version']}  {len(p['files'])} file(s){kind}")
    print(f"pyweb.lock: {len(lock['packages'])} package(s); files in static/vendor/")


def cmd_remove(args):
    from pyweb import packages
    lock = packages.install(_app_dir(args), remove=args.packages)
    print(f"removed {', '.join(args.packages)}; pyweb.lock: {len(lock['packages'])} package(s)")


def _parse_budget(spec):
    spec = spec.strip().lower()
    for suffix, mult in (("kb", 1024), ("k", 1024), ("mb", 1024 * 1024), ("m", 1024 * 1024), ("b", 1)):
        if spec.endswith(suffix):
            return int(float(spec[:-len(suffix)]) * mult)
    return int(float(spec))


def cmd_new(args):
    from pyweb.mcp import scaffold
    try:
        files = scaffold(args.name, template=args.template)
    except (FileExistsError, ValueError) as exc:
        raise SystemExit(f"error: {exc}")
    for f in files:
        print(f"created {f}")
    print(f"next: cd {args.name} && pyweb dev app.pyweb")


def cmd_lsp(args):
    """Language server for editors (stdio)."""
    from pyweb.lsp import serve_stdio
    serve_stdio()


def cmd_mcp(args):
    from pyweb.mcp import serve_stdio
    serve_stdio()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pyweb")
    ap.add_argument("--version", action="store_true",
                    help="print the PyWeb version and exit")
    sub = ap.add_subparsers(dest="cmd", required=False)
    p = sub.add_parser("inspect"); p.add_argument("file"); p.add_argument("--security", action="store_true", help="include security findings"); p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("build"); p.add_argument("file"); p.add_argument("--out", default="dist"); p.add_argument("--budget", action="append", default=[]); p.add_argument("--production", action="store_true", help="hashed assets, minified JS, split bundles, extracted CSS"); p.set_defaults(fn=cmd_build)
    p = sub.add_parser("dev"); p.add_argument("file"); p.add_argument("--port", type=int, default=8000); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--no-reload", action="store_true", help="disable hot-reload watcher"); p.set_defaults(fn=cmd_dev)
    p = sub.add_parser("serve"); p.add_argument("dir", default="dist", nargs="?"); p.add_argument("--host", default="0.0.0.0"); p.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8000), help="default: $PORT, else 8000"); p.add_argument("--app", default=None, help="live RPC factory module:attr"); p.add_argument("--migrate", action="store_true", help="apply pending migrations before serving (safe with many servers)"); p.add_argument("--workers", type=int, default=0, help="processes sharing the port (default: WEB_CONCURRENCY or 1)"); p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("db", help="migrations and seed data")
    p.add_argument("db_action", choices=["upgrade", "downgrade", "status", "diff", "new", "adopt", "squash", "seed", "migrate", "rollback"])
    p.add_argument("--app", default=None, help="the app whose Models to use (default: ./app.pyweb)")
    p.add_argument("--database", default=None, help="database URL (default: DATABASE_URL, then the app's)")
    p.add_argument("--migrations", default=None, help="migrations folder (default: next to the app)")
    p.add_argument("--name", default="migration")
    p.add_argument("--python", action="store_true", help="new: an empty Python migration instead of .sql files")
    p.add_argument("--steps", type=int, default=1, help="downgrade: how many applied migrations to revert")
    p.add_argument("--to", default=None, help="downgrade: revert everything applied after this label")
    p.add_argument("--target", default=None, help="upgrade: stop after this migration")
    p.add_argument("--contract", action="store_true", help="upgrade: also run contract (removal) steps")
    p.add_argument("--rename", action="append", default=[], metavar="TABLE.OLD=NEW", help="diff: a column rename")
    p.add_argument("--rename-table", action="append", default=[], metavar="OLD=NEW", help="diff: a table rename")
    p.add_argument("--allow-destructive", action="store_true", help="diff: put removals in the same migration")
    p.add_argument("--seeds", default=None, help="seed: the seeds file (default: seeds.py next to the app)")
    p.set_defaults(fn=cmd_db)
    p = sub.add_parser("worker", help="run background jobs and schedules")
    p.add_argument("app", nargs="?", default="app.pyweb", help="app.pyweb or a built dist/ (default: app.pyweb)")
    p.add_argument("--queues", default="", help="comma-separated queues (default: every queue the app uses)")
    p.add_argument("--concurrency", type=int, default=8, help="jobs at once (default 8)")
    p.add_argument("--grace", type=float, default=25.0, help="seconds running jobs get on shutdown")
    p.add_argument("--no-schedule", action="store_true", help="don't queue cron/interval jobs from this worker")
    p.set_defaults(fn=cmd_worker)
    p = sub.add_parser("jobs", help="list, retry and purge background jobs")
    p.add_argument("jobs_action", choices=["list", "retry", "purge", "run"])
    p.add_argument("ids", nargs="*", help="retry: job ids")
    p.add_argument("--app", default="app.pyweb")
    p.add_argument("--state", choices=["queued", "running", "done", "failed", "dead"], default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--days", type=float, default=7, help="purge: finished more than this many days ago")
    p.add_argument("--timeout", type=float, default=60, help="run: give up after this many seconds")
    p.set_defaults(fn=cmd_jobs)
    p = sub.add_parser("new"); p.add_argument("name"); p.add_argument("--template", default="counter", choices=["blank", "counter", "todo", "blog", "auth", "chat", "ai-chat"], help="starter app"); p.set_defaults(fn=cmd_new)
    p = sub.add_parser("mcp", help="run the MCP server (stdio) for AI assistants"); p.set_defaults(fn=cmd_mcp)
    p = sub.add_parser("lsp", help="run the language server (stdio) for editors"); p.set_defaults(fn=cmd_lsp)
    p = sub.add_parser("check"); p.add_argument("file")
    p.add_argument("--production", action="store_true", help="also check the environment is ready for production")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("dts", help="generate Python stubs from a TypeScript .d.ts file (experimental)")
    p.add_argument("dts"); p.add_argument("-o", "--out", default=None); p.set_defaults(fn=cmd_dts)
    p = sub.add_parser("add", help="add npm packages for browser code (no Node.js needed)")
    p.add_argument("packages", nargs="*", help="e.g. chart.js/auto canvas-confetti@^1.9 (none: reinstall from pyweb.lock)")
    p.add_argument("--app", default="app.pyweb", help="the app file; packages go next to it")
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("remove", help="remove npm packages added with `pyweb add`")
    p.add_argument("packages", nargs="+")
    p.add_argument("--app", default="app.pyweb")
    p.set_defaults(fn=cmd_remove)
    p = sub.add_parser("deploy", help="production files for docker, compose, k8s, fly, render or railway")
    p.add_argument("target_pos", nargs="?", metavar="TARGET", help="docker | compose | k8s | fly | render | railway")
    p.add_argument("--target", default=None, help=argparse.SUPPRESS)
    p.add_argument("--file", default="app.pyweb", help="the app (default: app.pyweb)")
    p.add_argument("--out", default="deploy")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--replicas", type=int, default=None, help="web machines/containers (default: 1, k8s: 2)")
    p.add_argument("--processes", type=int, default=None, help="server processes per machine (default 2)")
    p.add_argument("--db", choices=["sqlite", "postgres", "mysql"], default=None,
                   help="override the database choice (default: decided from the app and the target)")
    p.add_argument("--with", dest="with_services", default="", help="also provision: redis, postgres")
    p.add_argument("--domain", default="", help="the public domain (HTTPS, PYWEB_ORIGIN, Ingress)")
    p.add_argument("--region", default="", help="fly: primary region")
    p.add_argument("--name", default="", help="app name (default: the app folder's name)")
    p.add_argument("--plan", action="store_true", help="print the plan only, write nothing")
    p.add_argument("--check", action="store_true", help="fail if the plan has warnings (CI)")
    p.add_argument("--compose", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--db-url", default="", help="an existing database to use (provision none)")
    p.add_argument("--app", default="pyweb", help=argparse.SUPPRESS)
    p.add_argument("--image", default="", help="image name (default: NAME:latest)")
    p.set_defaults(fn=cmd_deploy)
    p = sub.add_parser("test"); p.add_argument("path", nargs="?", default=None); p.set_defaults(fn=cmd_test)
    p = sub.add_parser("fmt"); p.add_argument("path", nargs="?", default=None); p.set_defaults(fn=cmd_fmt)
    p = sub.add_parser("lint"); p.add_argument("path", nargs="?", default=None); p.set_defaults(fn=cmd_lint)
    args = ap.parse_args(argv)
    if args.version:
        from pyweb import __version__
        print(__version__)
        return
    if not args.cmd:
        ap.print_help()
        raise SystemExit(2)
    from pyweb.compiler.errors import CompileError
    from pyweb.packages import PackageError
    try:
        args.fn(args)
    except PackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except (CompileError, SyntaxError) as exc:
        if isinstance(exc, SyntaxError) and not isinstance(exc, CompileError):
            where = f"{exc.filename or getattr(args, 'file', '')}:{exc.lineno}" if exc.lineno else ""
            msg = getattr(exc, "pyweb_msg", None) or exc.msg
            print(f"error: {where}: {msg}" if where else f"error: {msg}", file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
