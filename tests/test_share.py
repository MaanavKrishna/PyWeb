"""``pyweb dev --share``: the tunnel's requests need the link's cookie, and never reach the dev tools."""

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from pyweb import cli, share as S

APP = 'from pyweb import App\napp = App()\n\n@app.page("/")\ndef Home():\n    n = 0\n\n    def inc():\n        n += 1\n    <button onclick={inc}>{n}</button>\n'
PUBLIC = "quiet-fox-12.trycloudflare.com"


@pytest.fixture
def gate():
    g = S.Share()
    g.url, g.host = f"https://{PUBLIC}", PUBLIC
    return g


def test_the_link_sets_a_cookie_and_nothing_else_gets_in(gate):
    host = {"Host": PUBLIC}
    assert gate.check("GET", "/", host)[0] == 403
    assert gate.check("GET", "/__pyweb/share?t=wrong", host)[0] == 403
    status, headers, _ = gate.check("GET", f"/__pyweb/share?t={gate.token}", host)
    cookie = dict(headers)["Set-Cookie"]
    assert status == 303 and dict(headers)["Location"] == "/"
    assert all(flag in cookie for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"))
    value = cookie.split(";")[0]
    assert gate.check("GET", "/", {**host, "Cookie": value}) is None
    assert gate.check("GET", f"/__pyweb/share?t={gate.token}", host)[0] == 303     # works for each person
    assert gate.check("GET", "/", {**host, "Cookie": "pyweb_share=forged"})[0] == 403
    assert gate.check("GET", "/", {"Host": "localhost:8000"}) is None              # you, on this machine


@pytest.fixture
def site(tmp_path, gate):
    from pyweb.hosting import Site
    (tmp_path / "app.pyweb").write_text(APP)
    state = cli._DevState(str(tmp_path / "app.pyweb"))
    cli._dev_load(state, Site)
    from pyweb.devlink import Link
    return cli._DevSite(state, Link(state), gate), gate


def _get(site, path, headers, client=("127.0.0.1", 5000), method="GET"):
    status, _h, body = site.respond(method, path, headers, b"{}", client=client)
    return status, body.decode("utf-8", "replace")


def test_guests_get_the_app_and_hot_reload_only(site):
    site, gate = site
    guest = {"Host": PUBLIC, "Cookie": f"{S.COOKIE}={gate.session}", "Sec-Fetch-Site": "same-origin"}
    status, page = _get(site, "/", guest)
    assert status == 200 and "/__pyweb/dev/changes" in page
    for tool in ("pyweb-devtools", "pyweb-inspect", "pyweb-assist", "__pywebTimeline", "__PYWEB_REQ__"):
        assert tool not in page, tool
    assert _get(site, "/__pyweb/dev/changes", guest)[0] == 200
    for path in ("/__pyweb/dev/requests", "/__pyweb/dev/theme", "/__pyweb/dev/mail", "/__pyweb/dev/agent/status",
                 "/__pyweb/dev/ai/status"):
        assert _get(site, path, guest)[0] == 403, path
    for path in ("/__pyweb/dev/ai/fix", "/__pyweb/dev/open", "/__pyweb/dev/theme", "/__pyweb/dev/welcome"):
        assert _get(site, path, guest, method="POST")[0] == 403, path
    assert site.websocket("/__pyweb/live", {"Host": PUBLIC}) == (403, "open the share link first")


def test_you_still_get_everything_and_the_lan_gets_hot_reload(site):
    site, gate = site
    status, page = _get(site, "/", {"Host": "localhost:8000"})
    assert status == 200 and "pyweb-devtools" in page and "pyweb-inspect" in page
    lan = {"Host": "192.168.1.20:8000"}
    status, page = _get(site, "/", lan, client=("192.168.1.30", 5000))
    assert status == 200 and "/__pyweb/dev/changes" in page and "pyweb-devtools" not in page
    assert _get(site, "/__pyweb/dev/requests", lan, client=("192.168.1.30", 5000))[0] == 403


def test_a_compile_error_page_for_guests_has_no_tools(site, tmp_path):
    site, gate = site
    site.state.error = SyntaxError("boom")
    guest = {"Host": PUBLIC, "Cookie": f"{S.COOKIE}={gate.session}"}
    status, page = _get(site, "/", guest)
    assert status == 500 and "/__pyweb/dev/changes" in page and "Fix with AI" not in page and "pyweb-assist" not in page
    assert "boom" not in page and "SyntaxError" not in page              # no error details either
    assert "boom" in _get(site, "/", {"Host": "localhost:8000"})[1]      # the developer sees them


FAKE = "import sys, time\nprint('INF Requesting new quick Tunnel on trycloudflare.com...', flush=True)\n" \
       "print('INF |  https://" + PUBLIC + "  |', flush=True)\ntime.sleep(60)\n"


def test_the_tunnel_url_is_read_from_its_output(tmp_path, monkeypatch):
    script = tmp_path / "tunnel.py"
    script.write_text(FAKE)
    monkeypatch.setenv("PYWEB_SHARE_COMMAND", f'"{sys.executable}" "{script}" {{port}}')
    g = S.Share()
    try:
        assert g.start(1234, timeout=10) == f"https://{PUBLIC}" and g.host == PUBLIC
        assert g.link() == f"https://{PUBLIC}/__pyweb/share?t={g.token}"
    finally:
        g.stop()
    script.write_text("print('no tunnel for you')\n")
    g = S.Share()
    assert g.start(1234, timeout=10) is None and "cloudflared" in g.error


def test_pyweb_dev_share_end_to_end(tmp_path):
    (tmp_path / "app.pyweb").write_text(APP)
    script = tmp_path / "tunnel.py"
    script.write_text(FAKE)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PYTHONPATH": root, "PYWEB_SHARE_COMMAND": f'"{sys.executable}" "{script}" {{port}}'}
    proc = subprocess.Popen([sys.executable, "-m", "pyweb.cli", "dev", "app.pyweb", "--port", str(port), "--share"],
                            cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        link = None
        end = time.time() + 30
        while link is None and time.time() < end:
            line = proc.stdout.readline()
            m = re.search(r"https://\S+/__pyweb/share\?t=\S+", line)
            link = m.group(0) if m else None
        assert link, "no share link printed"
        path = link.split(PUBLIC, 1)[1]

        def get(p, cookie=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{p}", headers={"Host": PUBLIC, **(
                {"Cookie": cookie} if cookie else {})})
            try:
                with opener.open(req, timeout=10) as r:
                    return r.status, dict(r.headers), r.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, dict(e.headers), e.read().decode()
        assert get("/")[0] == 403
        status, headers, _ = get(path)
        assert status == 303
        cookie = headers["Set-Cookie"].split(";")[0]
        status, _, page = get("/", cookie)
        assert status == 200 and "pyweb-devtools" not in page
        assert get("/__pyweb/dev/agent/status", cookie)[0] == 403
    finally:
        proc.terminate()
        proc.wait(15)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def test_a_tunnel_that_rewrites_host_is_still_a_guest(site):
    """ngrok --host-header=rewrite (or any proxy) sends `Host: localhost:<port>` from 127.0.0.1."""
    site, gate = site
    site.state.port = 8000
    for host in ("localhost", "localhost:80", "127.0.0.1:9999"):
        status, page = _get(site, "/", {"Host": host})
        assert status == 403 and "Ask for the link" in page, host
        assert _get(site, "/__pyweb/dev/requests", {"Host": host, "Cookie": f"{S.COOKIE}={gate.session}"})[0] == 403
    status, page = _get(site, "/", {"Host": "localhost", "Cookie": f"{S.COOKIE}={gate.session}"})
    assert status == 200 and "pyweb-devtools" not in page                 # with the link: the app, no tools
    status, page = _get(site, "/", {"Host": "localhost:8000"})
    assert status == 200 and "pyweb-devtools" in page                     # the developer


def test_source_maps_hold_server_code_so_only_the_developer_gets_them(site):
    site, gate = site
    _, page = _get(site, "/", {"Host": "localhost:8000"})
    js = re.search(r'src="(/static/[^"?]+\.js)', page).group(1)
    status, body = _get(site, js + ".map", {"Host": "localhost:8000"})
    assert status == 200 and "sourcesContent" in body
    guest = {"Host": PUBLIC, "Cookie": f"{S.COOKIE}={gate.session}", "Sec-Fetch-Site": "same-origin"}
    assert _get(site, js + ".map", guest)[0] == 404
    assert _get(site, js + ".map", {"Host": "192.168.1.20:8000"}, client=("192.168.1.30", 5000))[0] == 404
