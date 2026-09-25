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
    for spec in out["rpc"]:
        print(f"rpc {spec['name']}({', '.join(a['name']+': '+a['type'] for a in spec['args'])}) -> {spec['returns']} [{spec['location']}] line {spec['line']}")


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
    src = _load(args.file)
    out = compile_source(src, filename=args.file)
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
                p = dist + self.path
                if os.path.exists(p):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/javascript" if p.endswith(".js") else "text/html")
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
    with socketserver.TCPServer(("127.0.0.1", args.port), H) as httpd:
        httpd.serve_forever()


def cmd_new(args):
    os.makedirs(args.name, exist_ok=True)
    with open(f"{args.name}/app.pyweb", "w") as fh:
        fh.write('from pyweb import App\n\napp = App()\n\n@app.page("/")\ndef Home():\n    count = 0\n\n    def increment():\n        count += 1\n\n    <main>\n        <h1>Counter</h1>\n        <button onclick={increment}>\n            Count: {count}\n        </button>\n    </main>\n')
    print(f"created {args.name}/app.pyweb")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pyweb")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect"); p.add_argument("file"); p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("build"); p.add_argument("file"); p.add_argument("--out", default="dist"); p.set_defaults(fn=cmd_build)
    p = sub.add_parser("dev"); p.add_argument("file"); p.add_argument("--port", type=int, default=8000); p.set_defaults(fn=cmd_dev)
    p = sub.add_parser("new"); p.add_argument("name"); p.set_defaults(fn=cmd_new)
    p = sub.add_parser("check"); p.add_argument("file"); p.set_defaults(fn=cmd_check)
    for name in ("test", "fmt", "lint", "deploy"):
        pp = sub.add_parser(name); pp.set_defaults(fn=lambda a, n=name: print(f"pyweb {n}: not yet implemented in prototype"))
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
