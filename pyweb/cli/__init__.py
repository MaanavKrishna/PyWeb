"""`pyweb` CLI: new / dev / build / test / check / inspect / deploy."""

from __future__ import annotations

import argparse
import json
import os
import sys

# The dev server and the production checks live in their own modules; their names stay importable
# from here (the desktop shell and older code use them).
from pyweb.checks import _topology_problems, production_problems  # noqa: F401
from pyweb.devserver import (  # noqa: F401
    _GUEST_ERROR,
    DEV_GUEST_JS,
    DEV_RELOAD_JS,
    _dev_load,
    _DevSite,
    _DevState,
    _error_overlay,
    _local,
    _note_for_ai,
    _watch_and_reload,
    _welcome_marker,
)


def _load(path):
    with open(path) as fh:
        return fh.read()


def _pretty_error(exc, path=None):
    """A compile error as terminal text: where, the code frame, a hint and a docs link."""
    from pyweb.compiler.errors import pretty
    path = getattr(exc, "filename", None) or path
    try:
        source = _load(path) if path else None
    except OSError:
        source = None
    return pretty(exc, source, path)


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


def cmd_doctor(args):
    """Is this project and this machine ready? One line per check, with a fix for each problem."""
    from pyweb import doctor
    raise SystemExit(doctor.main(args.file, production=args.production, as_json=args.json, port=args.port))


def cmd_check(args):
    from pyweb.compiler import compile_source
    from pyweb.security import check_source
    from pyweb.secret_scan import scan_app
    src = _load(args.file)
    findings = check_source(src, args.file)
    findings += scan_app(args.file)
    from pyweb import a11y, lsp_ui
    findings += a11y.check(src, args.file) + lsp_ui.check(src, args.file)
    try:
        out = compile_source(src, filename=args.file)
        n_sig = sum(len(p["signals"]) for p in out["pages"].values())
        print(f"ok: {len(out['pages'])} page(s), {n_sig} signal(s), {len(out['rpc'])} rpc(s)")
    except Exception as exc:  # noqa: BLE001
        from pyweb.compiler.errors import describe
        rec = describe(exc, src, args.file)
        findings.append({"kind": "compile-error", "line": rec.get("line") or 0, "message": rec["message"],
                         "detail": "\n".join(x for x in (rec.get("frame"), rec.get("hint") and "hint: " + rec["hint"],
                                                           rec.get("docs") and "docs: " + rec["docs"]) if x)})
    for f in findings:
        print(f"{f['kind']} {f.get('file') or args.file}:{f['line']}: {f['message']}")
        if f.get("detail"):
            print("    " + f["detail"].replace("\n", "\n    "))
    problems = production_problems(src, os.path.dirname(os.path.abspath(args.file))) \
        if getattr(args, "production", False) else []
    for p in problems:
        print(f"production: {p}")
    if getattr(args, "production", False) and not problems:
        print("production: ready")
    if problems or any(f["kind"] in ("secret-leak", "compile-error", "hardcoded-secret") for f in findings) \
            or (getattr(args, "strict", False) and findings):
        raise SystemExit(1)


def cmd_build_desktop(args):
    from pyweb.desktop import package as _pkg
    out = args.out if args.out != "dist" else "dist-desktop"
    try:
        out, name = _pkg.make_project(args.file, out, bundle=args.bundle, version=args.app_version,
                                      author=args.author, author_email=args.author_email,
                                      requires=args.requires, pyweb_requirement=args.pyweb)
    except ValueError as exc:
        raise SystemExit(f"build failed: {exc}") from None
    print(f"wrote the desktop project for {name} to {out}/ (Briefcase)")
    if args.package:
        made = _pkg.package(out)
        for path in made:
            print(f"installer: {path}")
    else:
        print(f"make an installer: cd {out} && briefcase package   (or add --package)")


def cmd_build(args):
    if getattr(args, "desktop", False):
        return cmd_build_desktop(args)
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
    from pyweb import sbom as _sbom
    app_dir = os.path.dirname(os.path.abspath(args.file))
    _sbom.write_for_app(app_dir, args.out, name=os.path.basename(app_dir) or "app")
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


def _lan_address():
    """This machine's address on the local network (no traffic is sent), or None."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") else ip


def _can_open_browser():
    env = os.environ
    if env.get("CI") or env.get("SSH_CONNECTION") or env.get("SSH_TTY") or env.get("PYWEB_NO_OPEN"):
        return False
    if not sys.stdout.isatty():
        return False
    return sys.platform in ("darwin", "win32") or bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def _announce(host, port, url, open_browser=True, out=None):
    """After the dev server starts: the network URL with a QR code for a phone, and open the browser."""
    out = out or sys.stdout
    if host in ("0.0.0.0", "::", ""):
        ip = _lan_address()
        if ip:
            lan = f"http://{ip}:{port}/"
            print(f"On your network: {lan}", file=out)
            if out.isatty():
                from pyweb import qr
                print(qr.terminal(lan), file=out)
    elif out.isatty():
        print("On a phone: pyweb dev --host 0.0.0.0 (same Wi-Fi) shows a QR code to scan", file=out)
    out.flush()
    if open_browser and _can_open_browser():
        import threading
        import webbrowser
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()


def cmd_dev(args):
    """Development server: compile on save, live reload, error overlay."""
    os.environ.setdefault("PYWEB_CACHE", "1")     # parse results and module code on disk: a fast first start
    from pyweb.hosting import Site
    from pyweb.net import server as _net

    from pyweb.devlink import Link
    state = _DevState(args.file)
    state.welcome = os.environ.get("PYWEB_WELCOME", "1") != "0" and not os.path.exists(_welcome_marker(args.file))
    link = Link(state)
    _dev_load(state, Site)
    if state.error is not None:
        print("error: " + _pretty_error(state.error, state.path), file=sys.stderr)
    host = getattr(args, "host", "127.0.0.1") or "127.0.0.1"
    share = None
    if getattr(args, "share", False):
        from pyweb.share import Share
        share = Share()

    def ready(port):
        state.port = port
        local = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
        url = f"http://{'localhost' if local in ('127.0.0.1', '::1') else local}:{port}/"
        print(f"PyWeb dev server: {url}  ({args.file})", flush=True)
        _announce(host, port, url, open_browser=not getattr(args, "no_open", False))
        if share is not None:
            from pyweb.share import wait_and_print
            wait_and_print(share, port, sys.stdout)
        link.publish(f"http://{local}:{port}/")             # for `pyweb mcp` (.pyweb/dev.json, token only)
        if not getattr(args, "no_reload", False):
            _watch_and_reload(state, Site)
        from pyweb import jobs
        jobs.start_embedded()

    _net.run(lambda: _DevSite(state, link, share), host=host, port=args.port, ready=ready)


def cmd_files(args):
    from pyweb import filegc
    _, _, models = _db_context(args)
    gone = filegc.collect(models or None, grace=args.grace, dry_run=args.dry_run)
    verb = "would delete" if args.dry_run else "deleted"
    print(f"{verb} {len(gone)} file(s) no row refers to" + ("".join(f"\n  {k}" for k in gone[:50]) if gone else ""))


def cmd_desktop(args):
    from pyweb.desktop import shell
    shell.main(args.file, dev=args.dev, smoke=args.smoke, gui=args.gui)


def cmd_push(args):
    from pyweb import push
    try:
        if args.push_action == "key":
            key = push.new_key()
            print("A new VAPID key for Web Push. Keep it secret, set it in production, and don't change it:")
            print("browsers tie each subscription to it.\n")
            print(f"PYWEB_VAPID_KEY={key}")
        else:
            print(push.public_key())
    except push.PushError as exc:
        raise SystemExit(str(exc)) from None


def cmd_openapi(args):
    from pyweb import openapi
    doc = openapi.generate(args.file, server_url=args.server)
    text = json.dumps(doc, indent=2, default=str)
    if args.out in (None, "-"):
        print(text)
    else:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}: {len(doc['paths'])} server function(s)")


def cmd_profile(args):
    from pyweb import profile as _profile
    try:
        _profile.run(args.file, seconds=args.seconds, out=args.out, port=args.port, mode=args.mode)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None


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
    config.require_secret("pyweb serve")
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

    free_threaded = not getattr(sys, "_is_gil_enabled", lambda: True)()

    def ready(port):
        note = " on a free-threaded Python (each process uses every core)" if free_threaded else ""
        print(f"serving {args.dir} on http://{args.host}:{port} with {workers} worker(s){note} (health: /healthz)",
              flush=True)

    _net.run(make_site, host=args.host, port=args.port, workers=workers, logger=logger, ready=ready)


def tests_path(path=None):
    """Where ``pyweb test`` runs pytest: the given file or folder, the folder of a ``.pyweb`` file,
    or the current folder (templates keep ``test_app.py`` next to ``app.pyweb``)."""
    target = os.path.abspath(path or ".")
    if target.endswith(".pyweb"):
        target = os.path.dirname(target)
    return target


def cmd_test(args):
    import importlib.util
    import subprocess
    import sys as _sys
    if importlib.util.find_spec("pytest") is None:
        raise SystemExit("pyweb test runs pytest, which isn't installed: pip install pytest")
    target = tests_path(getattr(args, "path", None))
    if not os.path.exists(target):
        raise SystemExit(f"pyweb test: no such file or folder: {args.path}")
    raise SystemExit(subprocess.call([_sys.executable, "-m", "pytest", target, "-q"]))


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
    from pyweb.jobs import worker as _worker
    if args.alive:
        # A container health check: no app loading, just "did a worker finish a round recently?"
        raise SystemExit(0 if _worker.alive(max_age=args.max_age) else 1)
    from pyweb import telemetry
    telemetry.logs.setup()
    telemetry.install()
    metrics_port = args.metrics_port or int(os.environ.get("PYWEB_METRICS_PORT") or 0)
    if metrics_port:
        telemetry.serve_metrics(metrics_port)
    _load_app_for_jobs(args.app)
    queues = [q.strip() for q in (args.queues or "").split(",") if q.strip()] or None
    worker = jobs.Worker(queues=queues, concurrency=args.concurrency, schedule=not args.no_schedule,
                         heartbeat=_worker.heartbeat_path())
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
        if action == "check":
            # Do the migrations cover the Models? (no database needed; for CI and editors)
            _, migrations, models = _db_context(args, need_models=True)
            gaps = _migrate.missing(models, migrations)
            if args.json:
                print(json.dumps({"ok": not gaps, "missing": gaps, "migrations": migrations}))
            elif gaps:
                print("the Models changed without a migration:\n" + "\n".join(f"  - {g}" for g in gaps)
                      + "\nrun `pyweb db diff` to write it")
            else:
                print("migrations match the Models")
            if gaps:
                raise SystemExit(1)
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
        elif action == "reencrypt":
            from pyweb import fieldcrypt
            if not models:
                raise SystemExit("error: pass --app app.pyweb (the app whose encrypted fields to rewrite)")
            done = fieldcrypt.reencrypt(db, models)
            print("re-encrypted with the current PYWEB_DATA_KEY: "
                  + (", ".join(f"{t} ({n} rows)" for t, n in done.items()) or "no encrypted fields"))
        elif action == "adopt":
            print(f"wrote {_migrate.adopt(db, migrations)} (recorded as applied; nothing changed)")
        elif action == "squash":
            print(f"wrote {_migrate.squash(db, migrations)}")
        elif action == "seed":
            from pyweb.db import seeds
            base = os.path.dirname(os.path.abspath(args.app)) if getattr(args, "app", None) else os.getcwd()
            from pyweb import models as _models
            previous = _models._state.db
            # --database / DATABASE_URL: the seeds write there. Without migrations yet (prototype
            # mode) tables are created on first use, as `pyweb dev` does.
            _models.use_database(db, auto_create=not os.path.isdir(migrations))
            try:
                ran = seeds.run(args.seeds or os.path.join(base, "seeds.py"), db=db)
            finally:
                _models._state.db = previous
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
    if args.target or args.compose:
        from pyweb.deprecation import deprecated
        deprecated(f"`pyweb deploy --target/--compose` is deprecated: use `pyweb deploy {target}`", stacklevel=2)
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


def _ask(question, default=None, choices=None):
    """Ask in the terminal until the answer is one of ``choices`` (a number or a name), else ``default``."""
    while True:
        hint = f" [{default}]" if default else ""
        try:
            answer = input(f"{question}{hint}: ").strip()
        except EOFError:
            answer = ""
        answer = answer or (default or "")
        if choices is None:
            if answer:
                return answer
            continue
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1]
        if answer in choices:
            return answer
        print(f"  pick a number from 1 to {len(choices)}, or a name")


def _new_interactively():
    """``pyweb new`` with no name, in a terminal: ask for the name, the template and the colours."""
    from pyweb._templates import DESCRIPTIONS, TEMPLATES
    from pyweb.ui.theme import PRESETS
    print("New PyWeb app\n")
    name = _ask("Name (a new folder)", "my-app")
    print("\nStarter:")
    for i, t in enumerate(TEMPLATES, 1):
        print(f"  {i:>2}. {t:<8} {DESCRIPTIONS.get(t, '')}")
    template = _ask("Pick one", "counter", list(TEMPLATES))
    themes = ["default", *PRESETS]
    print("\nColours:")
    for i, t in enumerate(themes, 1):
        swatch = PRESETS.get(t, {}).get("accent", "#6366f1")
        r, g, b = (int(swatch[k:k + 2], 16) for k in (1, 3, 5))
        print(f"  {i:>2}. \x1b[38;2;{r};{g};{b}m██\x1b[0m {t}")
    theme = _ask("Pick one", "default", themes)
    print()
    return name, template, None if theme == "default" else theme


def cmd_new(args):
    from pyweb.scaffold import scaffold
    name, template, theme = args.name, args.template or "counter", args.theme
    if name is None:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise SystemExit("error: give the app a name: pyweb new <name> [--template counter]")
        name, template, theme = _new_interactively()
    try:
        files = scaffold(name, template=template)
    except (FileExistsError, ValueError) as exc:
        raise SystemExit(f"error: {exc}")
    for f in files:
        print(f"created {f}")
    if theme:
        from pyweb.ui.source_edit import FIELDS, save_theme
        from pyweb.ui.theme import PRESETS
        app_file = os.path.join(name, "app.pyweb")
        code = save_theme(app_file, {k: PRESETS[theme].get(k) for k in FIELDS})
        print(f"theme {app_file}: {code}")
    print(f"next: cd {name} && pyweb dev app.pyweb")


def cmd_upgrade(args):
    """What the app needs to change for this version of PyWeb (and, with --fix, the safe rewrites)."""
    from pyweb import upgrade as U
    if args.fix:
        for path, notes in U.fix(args.file).items():
            for note in notes:
                print(f"fixed {path}: {note}")
    findings = U.check(args.file)
    if args.json:
        print(json.dumps([f.as_dict() for f in findings]))
    else:
        for f in findings:
            where = f"{f.file}:{f.line}" if f.line else f.file
            print(f"{'change' if f.action else 'note'} {where}: {f.message}"
                  + (f"  (pyweb upgrade --fix: {f.fix})" if f.fix and not args.fix else ""))
        todo = [f for f in findings if f.action]
        print(f"{len(todo)} change(s) to make" if todo else "nothing to change for this version")
    if [f for f in findings if f.action]:
        raise SystemExit(1)


def cmd_lsp(args):
    """Language server for editors (stdio)."""
    from pyweb.lsp import serve_stdio
    serve_stdio()


def cmd_mcp(args):
    if getattr(args, "http", False):
        from pyweb.mcp_http import serve_http
        serve_http(args.host, args.port, path=args.path, token=args.token)
        return
    from pyweb.mcp import serve_stdio
    serve_stdio()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pyweb")
    ap.add_argument("--version", action="store_true",
                    help="print the PyWeb version and exit")
    sub = ap.add_subparsers(dest="cmd", required=False)
    p = sub.add_parser("inspect"); p.add_argument("file"); p.add_argument("--security", action="store_true", help="include security findings"); p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("build"); p.add_argument("file"); p.add_argument("--out", default="dist"); p.add_argument("--budget", action="append", default=[]); p.add_argument("--production", action="store_true", help="hashed assets, minified JS, split bundles, extracted CSS")
    p.add_argument("--desktop", action="store_true", help="a desktop app project (Briefcase) in dist-desktop/")
    p.add_argument("--package", action="store_true", help="with --desktop: also build the installer for this OS")
    p.add_argument("--bundle", default="com.example", help="with --desktop: reversed domain (com.example)")
    p.add_argument("--app-version", default="0.1.0", help="with --desktop: the app's version (1.2.3)")
    p.add_argument("--author", default=None)
    p.add_argument("--author-email", default=None)
    p.add_argument("--requires", action="append", default=[], help="with --desktop: another package the app needs")
    p.add_argument("--pyweb", default=None, help="with --desktop: the PyWeb requirement (default: this version)")
    p.set_defaults(fn=cmd_build)
    p = sub.add_parser("dev"); p.add_argument("file"); p.add_argument("--port", type=int, default=8000); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--no-reload", action="store_true", help="disable hot-reload watcher"); p.add_argument("--no-open", action="store_true", help="don't open the browser"); p.add_argument("--share", action="store_true", help="a temporary public link (cloudflared or localhost.run) for someone else to try the app; your dev tools stay on this machine"); p.set_defaults(fn=cmd_dev)
    p = sub.add_parser("profile", help="serve the app and show where its time goes (a flame graph on Python 3.15+)")
    p.add_argument("file", nargs="?", default="app.pyweb")
    p.add_argument("--seconds", type=float, default=15.0, help="how long to sample (default 15)")
    p.add_argument("--out", default=None, help="report file (default profile.html, or profile.txt before 3.15)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--mode", choices=["cpu", "wall", "gil"], default="cpu",
                   help="cpu: running code; wall: also waiting (I/O, locks); gil: waiting for the GIL")
    p.set_defaults(fn=cmd_profile)
    p = sub.add_parser("files", help="uploaded files: clean deletes files no row refers to")
    p.add_argument("files_action", choices=["clean"])
    p.add_argument("--app", default=None, help="the app (default: ./app.pyweb)")
    p.add_argument("--database", default=None)
    p.add_argument("--migrations", default=None)
    p.add_argument("--grace", type=int, default=24 * 3600, help="only files older than this many seconds")
    p.add_argument("--dry-run", action="store_true", help="list what would be deleted")
    p.set_defaults(fn=cmd_files)
    p = sub.add_parser("ui", help="the UI kit: list components, add (copy) one into components/, diff a copy")
    p.add_argument("ui_action", choices=["list", "add", "diff"])
    p.add_argument("names", nargs="*")
    p.add_argument("--dir", default=".", help="the app's folder (default: here)")
    p.add_argument("--force", action="store_true", help="add: overwrite an existing copy")
    p.set_defaults(fn=lambda a: __import__("pyweb.ui.cli", fromlist=["main"]).main(a))
    p = sub.add_parser("desktop", help="run the app in its own window (needs pyweb-stack[desktop])")
    p.add_argument("file", nargs="?", default="app.pyweb")
    p.add_argument("--dev", action="store_true", help="reload when files change (development)")
    p.add_argument("--smoke", action="store_true", help="open the window, check the first page loads, close it")
    p.add_argument("--gui", default=None, help="force a pywebview backend: qt, gtk, edgechromium, cocoa")
    p.set_defaults(fn=cmd_desktop)
    p = sub.add_parser("push", help="Web Push: key makes a PYWEB_VAPID_KEY, public prints the public key")
    p.add_argument("push_action", choices=["key", "public"])
    p.set_defaults(fn=cmd_push)
    p = sub.add_parser("openapi", help="an OpenAPI 3.1 document for the app's server functions")
    p.add_argument("file")
    p.add_argument("--out", default="-", help="file to write (default: standard output)")
    p.add_argument("--server", default=None, help="the API's base URL, e.g. https://example.com")
    p.set_defaults(fn=cmd_openapi)
    p = sub.add_parser("serve"); p.add_argument("dir", default="dist", nargs="?"); p.add_argument("--host", default="0.0.0.0"); p.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8000), help="default: $PORT, else 8000"); p.add_argument("--app", default=None, help="live RPC factory module:attr"); p.add_argument("--migrate", action="store_true", help="apply pending migrations before serving (safe with many servers)"); p.add_argument("--workers", type=int, default=0, help="processes sharing the port (default: WEB_CONCURRENCY or 1)"); p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("db", help="migrations and seed data")
    p.add_argument("db_action", choices=["upgrade", "downgrade", "status", "diff", "check", "new", "adopt", "squash", "seed", "migrate",
                                              "rollback", "reencrypt"])
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
    p.add_argument("--json", action="store_true", help="check: machine-readable output")
    p.set_defaults(fn=cmd_db)
    p = sub.add_parser("upgrade", help="what to change for this PyWeb version; --fix applies the safe rewrites")
    p.add_argument("file", nargs="?", default="app.pyweb")
    p.add_argument("--check", action="store_true", help="only report (the default)")
    p.add_argument("--fix", action="store_true", help="apply the rewrites that can't change behaviour")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_upgrade)
    p = sub.add_parser("worker", help="run background jobs and schedules")
    p.add_argument("app", nargs="?", default="app.pyweb", help="app.pyweb or a built dist/ (default: app.pyweb)")
    p.add_argument("--queues", default="", help="comma-separated queues (default: every queue the app uses)")
    p.add_argument("--concurrency", type=int, default=8, help="jobs at once (default 8)")
    p.add_argument("--grace", type=float, default=25.0, help="seconds running jobs get on shutdown")
    p.add_argument("--no-schedule", action="store_true", help="don't queue cron/interval jobs from this worker")
    p.add_argument("--alive", action="store_true",
                   help="health check: exit 0 if a worker here finished a healthy round recently, else 1")
    p.add_argument("--max-age", type=float, default=90.0, help="--alive: seconds since the last round (default 90)")
    p.add_argument("--metrics-port", type=int, default=0,
                   help="serve Prometheus /metrics on this port (default: $PYWEB_METRICS_PORT, else off)")
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
    p = sub.add_parser("new", help="create an app (with no name, in a terminal, it asks)")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--template", default=None, choices=list(__import__("pyweb._templates", fromlist=["TEMPLATES"]).TEMPLATES), help="starter app (default counter; saas: teams, accounts, data, jobs, admin; desktop: a notes app in its own window)")
    p.add_argument("--theme", default=None, choices=list(__import__("pyweb._templates", fromlist=["THEME_PRESETS"]).THEME_PRESETS), help="a colour preset written into App(theme=...)")
    p.set_defaults(fn=cmd_new)
    p = sub.add_parser("mcp", help="run the MCP server for AI assistants (stdio, or --http)")
    p.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    p.add_argument("--host", default="127.0.0.1", help="--http: address (default 127.0.0.1; others need --token)")
    p.add_argument("--port", type=int, default=8765, help="--http: port (default 8765)")
    p.add_argument("--path", default="/mcp", help="--http: endpoint path (default /mcp)")
    p.add_argument("--token", default=None, help="--http: the bearer token clients send (default: PYWEB_MCP_TOKEN, else one kept in .pyweb/mcp-token)")
    p.set_defaults(fn=cmd_mcp)
    p = sub.add_parser("lsp", help="run the language server (stdio) for editors"); p.set_defaults(fn=cmd_lsp)
    p = sub.add_parser("doctor", help="check the project and the machine, with a fix for each problem")
    p.add_argument("file", nargs="?", default="app.pyweb")
    p.add_argument("--production", action="store_true", help="also check the environment for production")
    p.add_argument("--json", action="store_true", help="print the checks as JSON")
    p.add_argument("--port", type=int, default=8000, help="the port pyweb dev will use (default 8000)")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("check"); p.add_argument("file"); p.add_argument("--strict", action="store_true", help="exit 1 on any finding (security warnings too)")
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
        print("error: " + _pretty_error(exc, getattr(args, "file", None)), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
