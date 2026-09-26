"""QA-004: hashed static-asset contract.

- SSR references versioned URLs (``/static/<page>.js?v=<hash>``).
- The ``?v=`` URL resolves to the same bytes as the unversioned file
  (dev-server strips the query; production hosts ignore it) so hashed
  refs are HTTP-resolvable.
- Versioned responses carry immutable cache headers.
"""

import http.server
import threading
from urllib import request as urlrequest

from pyweb.compiler import compile_source

APP = "from pyweb import App\napp=App()\n@app.page('/')\ndef H():\n    count = 0\n    <h1>{count}</h1>\n"


def test_ssr_emits_versioned_script_ref():
    page = compile_source(APP)["pages"]["H"]
    assert "/static/H.js?v=" in page["html"]


def test_versioned_url_resolves_to_same_bytes(tmp_path):
    from pyweb import cli as _cli
    import argparse
    src = tmp_path / "app.pyweb"
    src.write_text(APP)
    out = tmp_path / "dist"
    _cli.cmd_build(argparse.Namespace(file=str(src), out=str(out), budget=[]))
    js = (out / "static" / "H.js").read_bytes()

    served = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            p = out / self.path.split("?")[0].lstrip("/")
            if p.is_file():
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript")
                if "?v=" in self.path:
                    self.send_header("Cache-Control",
                                     "public, max-age=31536000, immutable")
                self.end_headers()
                served["immutable"] = "immutable" in self.headers.get(
                    "Cache-Control", "")
                self.wfile.write(p.read_bytes())
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        plain = urlrequest.urlopen(base + "/static/H.js").read()
        page = compile_source(APP)["pages"]["H"]
        ver = page["html"].split("/static/H.js?v=", 1)[1].split('"', 1)[0]
        hashed = urlrequest.urlopen(base + f"/static/H.js?v={ver}").read()
        assert plain == hashed == js
        assert served["immutable"] is False  # plain URL: no immutable header
        with urlrequest.urlopen(base + f"/static/H.js?v={ver}") as res:
            assert "immutable" in res.headers.get("Cache-Control", "")
    finally:
        httpd.shutdown()
        httpd.server_close()
