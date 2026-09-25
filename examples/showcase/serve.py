"""Showcase dev server: compile app.pyweb once, serve SSR HTML + static + RPC."""

import hashlib
import http.server
import os
import shutil
import socketserver
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pyweb.compiler import compile_source  # noqa: E402
from pyweb.runtime.server import Request, Server  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 12000


def build():
    with open(os.path.join(HERE, "app.pyweb")) as fh:
        src = fh.read()
    with open(os.path.join(HERE, "style.css")) as fh:
        css = fh.read()
    from pyweb.compiler.codegen.emit_html import emit_page
    out = compile_source(src, filename="app.pyweb", title="PyWeb — One language. Every layer.")
    page = out["pages"]["Home"]
    rt_path = os.path.join(HERE, "..", "..", "pyweb", "runtime", "browser", "runtime.js")
    with open(rt_path) as fh:
        runtime_src = fh.read()
    v = hashlib.sha256((page["js"] + css + runtime_src).encode()).hexdigest()[:8]
    html = emit_page("/", "PyWeb — One language. Every layer.", page["ui"],
                     page["initial"], js_url=f"/static/Home.js?v={v}", css=css,
                     computeds=page["computeds"])
    dist = os.path.join(HERE, "dist")
    os.makedirs(dist + "/static", exist_ok=True)
    rv = hashlib.sha256(runtime_src.encode()).hexdigest()[:8]
    with open(dist + "/static/Home.js", "w") as fh:
        fh.write(page["js"].replace("./runtime.js", f"./runtime.js?v={rv}"))
    shutil.copy(os.path.join(HERE, "..", "..", "pyweb", "runtime", "browser", "runtime.js"),
                dist + "/static/runtime.js")
    with open(os.path.join(HERE, "style.css"), "rb") as fh:
        css_bytes = fh.read()
    server = Server({"pages": {"Home": dict(page, html=html, route="/")}, "rpc": out["rpc"]})
    return server, dist, html.encode(), css_bytes


def main():
    compiled, dist, html_bytes, css_bytes = build()

    # Register the demo RPC implementation.
    def search_products(q: str) -> str:
        return q
    compiled.register_rpc(search_products)

    class H(http.server.BaseHTTPRequestHandler):
        def _bytes(self, body: bytes, ctype: str):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path.startswith("/static/"):
                p = os.path.join(dist, path[1:])
                if os.path.exists(p):
                    with open(p, "rb") as fh:
                        self._bytes(fh.read(), "text/javascript")
                else:
                    self.send_response(404)
                    self.end_headers()
                return
            if path in ("/style.css",):
                self._bytes(css_bytes, "text/css")
                return
            resp = compiled.handle(Request("GET", "/"))
            self._bytes(resp.body.encode(), "text/html")

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            resp = compiled.handle(Request("POST", self.path, dict(self.headers), self.rfile.read(n)))
            body = resp.body.encode() if isinstance(resp.body, str) else resp.body
            self.send_response(resp.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", PORT), H) as httpd:
        print(f"serving showcase on http://0.0.0.0:{PORT}/", flush=True)
        httpd.serve_forever()


main()
