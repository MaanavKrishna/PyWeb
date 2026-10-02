"""`pyweb` CLI: new / dev / build / test / check / inspect / deploy."""

from __future__ import annotations

import argparse
import http.server
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
    for name, page in out["pages"].items():
        print(f"page {name} route={page['route']} signals={page['signals']} computeds={list(page['computeds'])}")
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
    if any(f["kind"] in ("secret-leak", "compile-error") for f in findings):
        raise SystemExit(1)


def cmd_build(args):
    from pyweb.compiler import compile_source
    from pyweb.build import build as _build
    src = _load(args.file)
    out = compile_source(src, filename=args.file)
    production = getattr(args, "production", False)
    manifest = _build(out, args.out, source=src, production=production,
                      app_dir=os.path.dirname(os.path.abspath(args.file)))
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


def cmd_dev(args):
    """Development server: compile on save, live reload, error overlay."""
    from pyweb.hosting import Site
    from pyweb.serve import ThreadedServer

    state = _DevState(args.file)
    _dev_load(state, Site)
    if state.error is not None:
        print(f"error: {state.error}", file=sys.stderr)

    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, status, headers, body):
            self.send_response(status)
            for k, v in headers:
                if k.lower() != "content-length":
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _handle(self, method, body=b""):
            if self.path == "/__pyweb/dev/version":
                return self._send(200, [("Content-Type", "text/plain"), ("Cache-Control", "no-store")],
                                  str(state.version).encode())
            if state.error is not None or state.site is None:
                html = _error_overlay(state.error, state.path).encode()
                return self._send(500, [("Content-Type", "text/html; charset=utf-8")], html)
            status, headers, raw = state.site.respond(method, self.path, dict(self.headers), body)
            ctype = next((v for k, v in headers if k.lower() == "content-type"), "")
            if ctype.startswith("text/html") and b"</body>" in raw:
                raw = raw.replace(b"</body>", DEV_RELOAD_JS.encode() + b"</body>", 1)
            return self._send(status, headers, raw)

        def do_GET(self):  # noqa: N802
            self._handle("GET")

        def do_HEAD(self):  # noqa: N802
            self._handle("HEAD")

        def do_POST(self):  # noqa: N802
            try:
                n = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                n = 0
            self._handle("POST", self.rfile.read(n) if n else b"")

        def log_message(self, *a):
            pass

    host = getattr(args, "host", "127.0.0.1") or "127.0.0.1"
    with ThreadedServer((host, args.port), H) as httpd:
        print(f"PyWeb dev server: http://{host}:{httpd.server_address[1]}/  ({args.file})", flush=True)
        if not getattr(args, "no_reload", False):
            _watch_and_reload(state, Site)
        httpd.serve_forever()


def _watch_and_reload(state, Site):
    """Poll the app file and sibling ``.py``/``static`` files; reload on change."""
    import threading
    import time

    app_dir = os.path.dirname(os.path.abspath(state.path))

    def snapshot():
        stamps = {}
        for root, dirs, files in os.walk(app_dir):
            dirs[:] = [d for d in dirs if not d.startswith((".", "__pycache__", "node_modules", "dist"))]
            for fn in files:
                if fn.endswith((".pyweb", ".py", ".css", ".js")):
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
    from pyweb import serve as _serve
    from pyweb import observability as _obs
    httpd = _serve.serve(args.dir, host=args.host, port=args.port,
                         app_factory=args.app, logger=_obs.Logger("serve"))
    addr = httpd.server_address
    print(f"serving {args.dir} on http://{addr[0]}:{addr[1]} "
          f"(health: /healthz)")
    _serve.install_shutdown_handlers(httpd, logger=_obs.Logger("serve"))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


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


def cmd_db(args):
    from pyweb.db import migrate as _migrate
    action = args.db_action
    if action == "migrate":
        applied = _migrate.migrate(args.database or os.environ.get("DATABASE_URL", ":memory:"),
                                   args.migrations)
        print(f"applied {len(applied)} migration(s): "
              + (", ".join(applied) if applied else "already up to date"))
    elif action == "new":
        path = _migrate.new_migration(args.migrations, args.name)
        print(f"created {path}")
    elif action == "status":
        rows = _migrate.status(args.database or os.environ.get("DATABASE_URL", ":memory:"),
                               args.migrations)
        for name, applied in rows:
            print(f"[{'x' if applied else ' '}] {name}")
    elif action == "rollback":
        try:
            rolled = _migrate.rollback(
                args.database or os.environ.get("DATABASE_URL", ":memory:"),
                args.migrations, steps=args.steps, to=args.to)
        except ValueError as exc:
            raise SystemExit(f"error: {exc}")
        print("rolled back: " + (", ".join(rolled) if rolled else "nothing to roll back"))
    else:
        raise SystemExit(f"unknown db action {action!r}")


def cmd_deploy(args):
    from pyweb import deploy as D
    if not getattr(args, "db_url", None) and not os.environ.get("DATABASE_URL"):
        print("note: no DATABASE_URL set (--db-url or env); "
              "deploying with embedded sqlite", file=sys.stderr)
    target = (args.target or "docker").lower()
    outdir = args.out
    os.makedirs(outdir, exist_ok=True)
    files = {}
    if target in ("docker", "compose"):
        files["Dockerfile"] = D.dockerfile(port=args.port)
        if target == "compose" or args.compose:
            files["compose.yaml"] = D.compose(port=args.port, db_url=args.db_url or "")
    elif target == "k8s":
        files["k8s.yaml"] = D.k8s_manifest(app=args.app, image=args.image, port=args.port)
    else:
        raise SystemExit(f"unknown deploy target {args.target!r} (docker|compose|k8s)")
    for name, body in files.items():
        with open(os.path.join(outdir, name), "w") as fh:
            fh.write(body)
    print(f"deploy {target} -> {outdir}/ ({', '.join(files)})")


def cmd_npm(args):
    from pyweb.npm import npm_main
    raise SystemExit(npm_main([args.dts] + (["-o", args.out] if args.out else [])))


def _parse_budget(spec):
    spec = spec.strip().lower()
    for suffix, mult in (("kb", 1024), ("k", 1024), ("mb", 1024 * 1024), ("m", 1024 * 1024), ("b", 1)):
        if spec.endswith(suffix):
            return int(float(spec[:-len(suffix)]) * mult)
    return int(float(spec))


NEW_APP = """from pyweb import App

app = App(title="{title}")


@app.page("/")
def Home():
    count = 0

    def increment():
        count += 1

    <main>
        <h1>Counter</h1>
        <button onclick={{increment}}>
            Count: {{count}}
        </button>
    </main>
"""


def cmd_new(args):
    if os.path.exists(os.path.join(args.name, "app.pyweb")):
        raise SystemExit(f"{args.name}/app.pyweb already exists")
    os.makedirs(os.path.join(args.name, "static"), exist_ok=True)
    title = os.path.basename(os.path.abspath(args.name)).replace("-", " ").replace("_", " ").title()
    with open(os.path.join(args.name, "app.pyweb"), "w", encoding="utf-8") as fh:
        fh.write(NEW_APP.format(title=title))
    with open(os.path.join(args.name, ".gitignore"), "w", encoding="utf-8") as fh:
        fh.write("dist/\n__pycache__/\n*.db\n")
    print(f"created {args.name}/app.pyweb\nnext: cd {args.name} && pyweb dev app.pyweb")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pyweb")
    ap.add_argument("--version", action="store_true",
                    help="print the PyWeb version and exit")
    sub = ap.add_subparsers(dest="cmd", required=False)
    p = sub.add_parser("inspect"); p.add_argument("file"); p.add_argument("--security", action="store_true", help="include security findings"); p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("build"); p.add_argument("file"); p.add_argument("--out", default="dist"); p.add_argument("--budget", action="append", default=[]); p.add_argument("--production", action="store_true", help="hashed assets, minified JS, split bundles, extracted CSS"); p.set_defaults(fn=cmd_build)
    p = sub.add_parser("dev"); p.add_argument("file"); p.add_argument("--port", type=int, default=8000); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--no-reload", action="store_true", help="disable hot-reload watcher"); p.set_defaults(fn=cmd_dev)
    p = sub.add_parser("serve"); p.add_argument("dir", default="dist", nargs="?"); p.add_argument("--host", default="0.0.0.0"); p.add_argument("--port", type=int, default=8000); p.add_argument("--app", default=None, help="live RPC factory module:attr"); p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("db"); p.add_argument("db_action", choices=["migrate", "new", "status", "rollback"]); p.add_argument("--database", default=None); p.add_argument("--migrations", default="migrations"); p.add_argument("--name", default="migration"); p.add_argument("--steps", type=int, default=1, help="rollback: how many applied migrations to revert"); p.add_argument("--to", default=None, help="rollback: revert everything applied after this label"); p.set_defaults(fn=cmd_db)
    p = sub.add_parser("new"); p.add_argument("name"); p.set_defaults(fn=cmd_new)
    p = sub.add_parser("check"); p.add_argument("file"); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("npm"); p.add_argument("dts"); p.add_argument("-o", "--out", default=None); p.set_defaults(fn=cmd_npm)
    p = sub.add_parser("deploy")
    p.add_argument("--target", default="docker")
    p.add_argument("--out", default="deploy")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--compose", action="store_true")
    p.add_argument("--db-url", default="")
    p.add_argument("--app", default="pyweb")
    p.add_argument("--image", default="pyweb:latest")
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
    try:
        args.fn(args)
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
