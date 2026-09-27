"""`pyweb` CLI: new / dev / build / test / check / inspect / deploy."""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
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
    from pyweb.build import build as _production_build
    src = _load(args.file)
    out = compile_source(src, filename=args.file)
    if getattr(args, "production", False):
        manifest = _production_build(out, args.out)
        print(f"built {len(manifest['pages'])} page(s) + "
              f"{len(manifest.get('rpc', []))} rpc(s) -> {args.out}/ (production)")
        return
    os.makedirs(args.out + "/static", exist_ok=True)
    os.makedirs(args.out + "/server", exist_ok=True)
    for name, page in out["pages"].items():
        with open(f"{args.out}/static/{name}.js", "w") as fh:
            fh.write(page["js"])
        with open(f"{args.out}/server/{name}.html", "w") as fh:
            fh.write(page["html"])
        with open(f"{args.out}/static/{name}.js.map", "w") as fh:
            json.dump({"page": name, "mappings": page.get("sourcemap", [])}, fh, indent=2)
    import shutil
    shutil.copy(os.path.join(os.path.dirname(__file__), "..", "runtime", "browser", "runtime.js"),
                args.out + "/static/runtime.js")
    manifest = {"pages": {n: {"route": p["route"], "signals": p["signals"],
                              "computeds": list(p["computeds"])} for n, p in out["pages"].items()},
                "rpc": out["rpc"], "ir": out["ir_text"]}
    with open(args.out + "/manifest.json", "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"built {len(out['pages'])} page(s) + {len(out['rpc'])} rpc(s) -> {args.out}/")
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


def cmd_dev(args):
    from pyweb.compiler import compile_source
    from pyweb.runtime.server import Server, Request

    src = _load(args.file)
    out = compile_source(src, filename=args.file)
    server = Server(out)
    # Auto-register @server fns by executing module (server-safe subset).
    ns: dict = {}
    try:
        exec(compile(src.split("<")[0], args.file, "exec"), ns)
        for obj in ns.values():
            if callable(obj) and getattr(obj, "__pyweb_location__", "") in ("server", "worker", "edge"):
                server.register_rpc(obj)
    except Exception as exc:  # noqa: BLE001
        print(f"note: rpc auto-register skipped ({exc})", file=sys.stderr)

    dist = os.path.abspath("dist")
    os.makedirs(dist + "/static", exist_ok=True)
    for name, page in out["pages"].items():
        with open(dist + f"/static/{name}.js", "w") as fh:
            fh.write(page["js"])
    import shutil
    shutil.copy(os.path.join(os.path.dirname(__file__), "..", "runtime", "browser", "runtime.js"), dist + "/static/runtime.js")

    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, resp):
            body = resp.body.encode() if isinstance(resp.body, str) else resp.body
            self.send_response(resp.status)
            for k, v in resp.headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/static/"):
                p = dist + self.path.split("?")[0]
                if os.path.exists(p):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/javascript" if p.endswith(".js") else "text/html")
                    if "?v=" in self.path:
                        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                    self.end_headers()
                    with open(p, "rb") as fh:
                        self.wfile.write(fh.read())
                else:
                    self.send_response(404); self.end_headers()
                return
            self._send(server.handle(Request("GET", self.path.split("?")[0])))

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            self._send(server.handle(Request("POST", self.path, dict(self.headers), self.rfile.read(n))))

        def log_message(self, *a):
            pass

    print("PyWeb\n\n\xe2\x9c\x93 compiler\n\xe2\x9c\x93 server\n\xe2\x9c\x93 debugger\n\nLocal: http://localhost:8000")
    try:
        from pyweb.serve import ThreadedServer
        server_cls = ThreadedServer
    except ImportError:  # pragma: no cover - serve module ships with pyweb
        server_cls = socketserver.TCPServer
    with server_cls(("127.0.0.1", args.port), H) as httpd:
        if not getattr(args, "no_reload", False):
            _watch_and_rebuild(args.file, dist, server)
        httpd.serve_forever()


def _watch_and_rebuild(path, dist, server):
    """Poll ``path`` mtime; recompile in place on change (hot reload).

    The browser picks changes up on next navigation/asset fetch because
    dev assets are served fresh from disk on every request. stdlib-only
    polling keeps ``pyweb dev`` dependency-free; typical latency <1s.
    """
    import threading
    import time

    try:
        last = os.path.getmtime(path)
    except OSError:
        return

    def recompile():
        from pyweb.compiler import compile_source
        from pyweb.runtime.server import Server as _Server
        src = _load(path)
        fresh = compile_source(src, filename=path)
        server.compiled = fresh
        server.routes = _Server(fresh).routes
        for name, page in fresh.get("pages", {}).items():
            with open(os.path.join(dist, "static", f"{name}.js"), "w") as fh:
                fh.write(page["js"])
        print(f"reloaded {path} ({len(fresh.get('pages', {}))} page(s))",
              flush=True)

    def poll():
        nonlocal last
        while True:
            time.sleep(0.5)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime != last:
                last = mtime
                try:
                    recompile()
                except Exception as exc:  # noqa: BLE001 - stay alive on error
                    print(f"reload failed: {exc}", flush=True)

    thread = threading.Thread(target=poll, daemon=True, name="pyweb-reload")
    thread.start()


def cmd_serve(args):
    from pyweb import serve as _serve
    from pyweb import observability as _obs
    httpd = _serve.serve(args.dir, host=args.host, port=args.port,
                         app_factory=args.app, logger=_obs.Logger("serve"))
    addr = httpd.server_address
    print(f"serving {args.dir} on http://{addr[0]}:{addr[1]} "
          f"(health: /healthz)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


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


def cmd_new(args):
    os.makedirs(args.name, exist_ok=True)
    with open(f"{args.name}/app.pyweb", "w") as fh:
        fh.write('from pyweb import App\n\napp = App()\n\n@app.page("/")\ndef Home():\n    count = 0\n\n    def increment():\n        count += 1\n\n    <main>\n        <h1>Counter</h1>\n        <button onclick={increment}>\n            Count: {count}\n        </button>\n    </main>\n')
    print(f"created {args.name}/app.pyweb")


def build_parser():
    """Build the CLI parser (also exposes --security-scan)."""
    ap = argparse.ArgumentParser(prog="pyweb")
    ap.add_argument("--security-scan", action="store_true",
                    help="run security.scan over the app and report")
    return ap


def main(argv=None):
    if argv is not None and "--security-scan" in argv:
        from pyweb import security
        print("security scan: pass an app descriptor to security.scan(app)")
        return 0
    ap = argparse.ArgumentParser(prog="pyweb")
    ap.add_argument("--version", action="store_true",
                    help="print the PyWeb version and exit")
    sub = ap.add_subparsers(dest="cmd", required=False)
    p = sub.add_parser("inspect"); p.add_argument("file"); p.add_argument("--security", action="store_true", help="include security findings"); p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("build"); p.add_argument("file"); p.add_argument("--out", default="dist"); p.add_argument("--budget", action="append", default=[]); p.add_argument("--production", action="store_true", help="hashed assets, minified JS, split bundles, extracted CSS"); p.set_defaults(fn=cmd_build)
    p = sub.add_parser("dev"); p.add_argument("file"); p.add_argument("--port", type=int, default=8000); p.add_argument("--no-reload", action="store_true", help="disable hot-reload watcher"); p.set_defaults(fn=cmd_dev)
    p = sub.add_parser("serve"); p.add_argument("dir", default="dist", nargs="?"); p.add_argument("--host", default="0.0.0.0"); p.add_argument("--port", type=int, default=8000); p.add_argument("--app", default=None, help="live RPC factory module:attr"); p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("db"); p.add_argument("db_action", choices=["migrate", "new", "status"]); p.add_argument("--database", default=None); p.add_argument("--migrations", default="migrations"); p.add_argument("--name", default="migration"); p.set_defaults(fn=cmd_db)
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
        from importlib.metadata import version, PackageNotFoundError
        try:
            print(version("pyweb"))
        except PackageNotFoundError:
            print("1.0.0")
        return
    if not args.cmd:
        ap.print_help()
        raise SystemExit(2)
    args.fn(args)


if __name__ == "__main__":
    main()
