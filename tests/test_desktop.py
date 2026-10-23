"""Desktop apps without a window: the front door (launch token, cookie, Host, Origin), the data
folder and secrets, the server-side API, platform flags, the shell's wiring (with a stand-in
pywebview) and the Briefcase project. The real window is in tests/desktop/."""

import json
import os
import stat
import struct
import sys
import types

import pytest

from pyweb import desktop, platform
from pyweb.desktop import gate as G
from pyweb.desktop import package, shell
from pyweb.hosting import Site
from pyweb.testing import TestClient

APP = '''
from pyweb import App, server, platform
from pyweb.desktop import Window
app = App(title="Field Notes", desktop=Window(width=900, height=600, start_url="/notes"))


@server
def ping() -> str:
    return "pong"


@app.page("/notes")
def Notes():
    where = "desktop" if platform.desktop else "web"
    <p id="where">{where}</p>
'''

SHELL_ENV = ("PYWEB_DESKTOP", "PYWEB_ENV", "PYWEB_DESKTOP_NAME", "PYWEB_AUTH_SECRET", "PYWEB_DATA_KEY")


@pytest.fixture
def clean_env(monkeypatch):
    """shell.run() sets these in os.environ: make sure each is put back afterwards."""
    for name in SHELL_ENV:
        monkeypatch.setenv(name, "placeholder")      # recorded, so undo restores (or removes) it
        monkeypatch.delenv(name)
    return monkeypatch


PORT = 50123
HOST = f"127.0.0.1:{PORT}"


@pytest.fixture
def app_file(tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(APP)
    return str(path)


@pytest.fixture
def door(app_file, monkeypatch):
    monkeypatch.setenv("PYWEB_DESKTOP", "1")
    d = G.DesktopSite(Site(app_file), start_url="/notes")
    d.bind(PORT)
    return d


def enter(d):
    """Open the window's first URL; returns the cookie header to send afterwards."""
    status, headers, _ = d.respond("GET", d.launch_url()[len(d.origin):], {"Host": HOST})
    assert status == 303
    h = dict(headers)
    assert h["Location"] == "/notes"
    cookie = h["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    return cookie.split(";", 1)[0]


# ------------------------------------------------------------------ the front door

def test_the_window_gets_in_once(door):
    cookie = enter(door)
    status, _, body = door.respond("GET", "/notes", {"Host": HOST, "Cookie": cookie})
    assert status == 200 and b'<p id="where">desktop</p>' in body
    assert b'src="/__pyweb/desktop.js"' in body
    # The launch token works once: another program that read it later gets nothing.
    status, _, _ = door.respond("GET", door.launch_url()[len(door.origin):], {"Host": HOST})
    assert status == 403
    status, _, _ = door.respond("GET", "/__pyweb/desktop/start?t=wrong", {"Host": HOST})
    assert status == 403


def test_everything_needs_the_cookie(door):
    enter(door)
    for method, path in (("GET", "/notes"), ("POST", "/__pyweb/rpc/ping"), ("GET", "/static/runtime.js"),
                         ("GET", "/healthz"), ("GET", "/__pyweb/metrics"), ("GET", "/__pyweb/desktop.js"),
                         ("OPTIONS", "/notes")):
        status, headers, body = door.respond(method, path, {"Host": HOST, "Content-Type": "application/json"}, b"{}")
        assert status == 403, (method, path)
        assert b"Open this app from its window" in body
    assert door.websocket("/__pyweb/ws", {"Host": HOST}) == (403, "not the app's window")
    status, _, _ = door.respond("GET", "/notes", {"Host": HOST, "Cookie": "pyweb_desktop=guess"})
    assert status == 403


def test_host_and_origin_are_checked(door):
    cookie = enter(door)
    ok = {"Host": HOST, "Cookie": cookie}
    for bad in ({"Host": f"evil.example:{PORT}"}, {"Host": f"localhost:{PORT}"}, {"Host": "127.0.0.1:1"},
                {"Origin": "https://evil.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}):
        status, _, _ = door.respond("GET", "/notes", {**ok, **bad})
        assert status == 403, bad
    assert door.websocket("/__pyweb/ws", {**ok, "Origin": "https://evil.example"})[0] == 403
    status, _, body = door.respond("POST", "/__pyweb/rpc/ping", {**ok, "Origin": door.origin,
                                                                "Content-Type": "application/json"}, b"{}")
    assert status == 200 and json.loads(body)["result"] == "pong"
    # The launch URL checks Host and Origin too (a web page can't start a session).
    fresh = G.DesktopSite(door.site)
    fresh.bind(PORT)
    status, _, _ = fresh.respond("GET", fresh.launch_url()[len(fresh.origin):],
                                 {"Host": HOST, "Origin": "https://evil.example"})
    assert status == 403


def test_no_cors(door):
    cookie = enter(door)
    for method in ("GET", "OPTIONS", "POST"):
        _, headers, _ = door.respond(method, "/notes", {"Host": HOST, "Cookie": cookie,
                                                        "Access-Control-Request-Method": "POST"})
        assert not [k for k, _ in headers if k.lower().startswith("access-control-")]


def test_links_open_outside_the_window(door, monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    cookie = enter(door)
    h = {"Host": HOST, "Cookie": cookie, "Content-Type": "application/json"}
    assert door.respond("POST", G.OPEN, h, json.dumps({"url": "https://pyweb.dev/docs"}).encode())[0] == 204
    assert door.respond("POST", G.OPEN, h, json.dumps({"url": "mailto:hi@example.com"}).encode())[0] == 204
    for bad in ("file:///etc/passwd", "javascript:alert(1)", "smb://host/share", "https://x\r\nY", None):
        assert door.respond("POST", G.OPEN, h, json.dumps({"url": bad}).encode())[0] == 422, bad
    assert door.respond("GET", G.OPEN, h)[0] == 405
    assert door.respond("POST", G.OPEN, {**h, "Content-Type": "text/plain"}, b"x")[0] == 415
    assert door.respond("POST", G.OPEN, {"Host": HOST, "Content-Type": "application/json"},
                        b'{"url": "https://a.example"}')[0] == 403
    assert opened == ["https://pyweb.dev/docs", "mailto:hi@example.com"]


def test_the_page_reports_itself(door):
    cookie = enter(door)
    assert not door.ready.is_set()
    status, _, _ = door.respond("POST", G.READY, {"Host": HOST, "Cookie": cookie, "Content-Type": "application/json"},
                                b'{"title": "Field Notes", "path": "/notes", "root": true}')
    assert status == 204 and door.ready.is_set()
    assert door.pages[-1] == {"title": "Field Notes", "path": "/notes", "root": True}
    script = door.respond("GET", G.SCRIPT, {"Host": HOST, "Cookie": cookie})
    assert script[0] == 200 and b"/__pyweb/desktop/open" in script[2]


def test_desktop_script_has_no_html_sinks():
    src = open(os.path.join(G.BROWSER_DIR, "desktop.js"), encoding="utf-8").read()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in src


def test_desktop_script_only_in_the_desktop_app(app_file, monkeypatch):
    monkeypatch.delenv("PYWEB_DESKTOP", raising=False)
    page = TestClient(app_file).get("/notes").text
    assert "desktop.js" not in page and '<p id="where">web</p>' in page


# ------------------------------------------------------------------ data folder and secrets

def test_data_folder_per_os():
    home = "/home/ann"
    env = {"HOME": home}
    assert desktop.data_dir("Field Notes", platform="linux", environ=env) == f"{home}/.local/share/field-notes"
    assert desktop.data_dir("Field Notes", platform="linux", environ={**env, "XDG_DATA_HOME": "/x"}) == \
        "/x/field-notes"
    assert desktop.data_dir("Field Notes", platform="darwin", environ=env) == \
        f"{home}/Library/Application Support/Field Notes"
    assert desktop.data_dir("Field Notes", platform="win32", environ={**env, "APPDATA": r"C:\Users\ann\AppData\Roaming"}) \
        .endswith("Field Notes")
    assert desktop.data_dir("x", environ={"PYWEB_DESKTOP_DATA": "/data"}) == "/data"
    assert desktop.safe_name('a<b>:"c/d\\e|f?*') == "abcdef"
    assert desktop.safe_name("...") == "PyWeb app"


def test_app_name():
    assert shell.app_name(os.devnull) != ""


def test_app_name_from_title(app_file, tmp_path):
    assert shell.app_name(app_file) == "Field Notes"
    other = tmp_path / "my_notes" / "app.pyweb"
    other.parent.mkdir()
    other.write_text("from pyweb import App\napp = App()\n")
    assert shell.app_name(str(other)) == "My Notes"


def test_secrets_are_made_once_and_kept_private(tmp_path):
    env = {}
    first = shell.load_secrets(str(tmp_path), env)
    assert len(env["PYWEB_AUTH_SECRET"]) >= 32 and len(env["PYWEB_DATA_KEY"]) >= 32
    assert env["PYWEB_AUTH_SECRET"] != env["PYWEB_DATA_KEY"]
    path = tmp_path / "secrets.json"
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    again = {}
    assert shell.load_secrets(str(tmp_path), again) == first and again == env       # the same next launch
    mine = {"PYWEB_AUTH_SECRET": "from-the-environment-" * 3}
    shell.load_secrets(str(tmp_path), mine)
    assert mine["PYWEB_AUTH_SECRET"].startswith("from-the-environment")             # the environment wins


def test_prepare(app_file, tmp_path):
    env = {"PYWEB_DESKTOP_DATA": str(tmp_path / "data")}
    folder = shell.prepare(app_file, environ=env)
    assert folder == str(tmp_path / "data") and os.path.isfile(os.path.join(folder, "secrets.json"))
    assert env["PYWEB_DESKTOP"] == "1" and env["PYWEB_ENV"] == "production"
    assert env["PYWEB_DESKTOP_NAME"] == "Field Notes"
    dev = {"PYWEB_DESKTOP_DATA": str(tmp_path / "data2")}
    shell.prepare(app_file, dev=True, environ=dev)
    assert "PYWEB_ENV" not in dev


def test_desktop_apps_create_their_tables_in_production(tmp_path, monkeypatch):
    monkeypatch.setenv("PYWEB_ENV", "production")
    monkeypatch.setenv("PYWEB_AUTH_SECRET", "s" * 40)
    monkeypatch.setenv("PYWEB_DESKTOP", "1")
    from pyweb import models as M
    monkeypatch.setattr(M._state, "db", None)
    path = tmp_path / "app.pyweb"
    path.write_text(f'from pyweb import App, Model, Field\napp = App(database="sqlite:///{tmp_path}/n.db")\n\n\n'
                    'class Note(Model):\n    text: str = Field(max=100)\n')
    from pyweb.app_loader import LoadedApp
    LoadedApp(str(path))
    import sqlite3
    tables = {r[0] for r in sqlite3.connect(tmp_path / "n.db").execute("select name from sqlite_master")}
    assert "notes" in tables or "note" in tables
    M._state.db = None


# ------------------------------------------------------------------ the API

def test_api_needs_the_desktop_app(monkeypatch):
    monkeypatch.delenv("PYWEB_DESKTOP", raising=False)
    for call in (lambda: desktop.open_file(), lambda: desktop.save_file("x.txt", "x"), desktop.choose_folder,
                 lambda: desktop.notify("hi"), lambda: desktop.reveal("/tmp"), lambda: desktop.open_url("https://a.b"),
                 desktop.window, desktop.data_dir):
        with pytest.raises(desktop.DesktopUnavailable):
            call()
    assert desktop.is_desktop() is False


class FakeWindow:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def create_file_dialog(self, kind, **kwargs):
        self.calls.append((kind, kwargs))
        return self.answers.pop(0)


@pytest.fixture
def fake_webview(monkeypatch):
    mod = types.ModuleType("webview")
    mod.FileDialog = types.SimpleNamespace(OPEN=10, FOLDER=20, SAVE=30)
    monkeypatch.setitem(sys.modules, "webview", mod)
    monkeypatch.setenv("PYWEB_DESKTOP", "1")
    return mod


def test_dialogs(fake_webview, monkeypatch, tmp_path):
    win = FakeWindow([("/a.txt",), None, ("/a.txt", "/b.txt"), str(tmp_path / "out.txt"), None, ("/docs",)])
    monkeypatch.setattr(desktop.SHELL, "window", win)
    assert desktop.open_file(("Text (*.txt)",)) == "/a.txt"
    assert desktop.open_file() is None
    assert desktop.open_file(multiple=True) == ["/a.txt", "/b.txt"]
    assert desktop.save_file("notes.txt", "héllo") == str(tmp_path / "out.txt")
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "héllo"
    assert desktop.save_file("x.txt", b"x") is None
    assert desktop.choose_folder() == "/docs"
    kinds = [k for k, _ in win.calls]
    assert kinds == [10, 10, 10, 30, 30, 20]
    assert win.calls[0][1]["file_types"] == ("Text (*.txt)",) and win.calls[3][1]["save_filename"] == "notes.txt"
    monkeypatch.setattr(desktop.SHELL, "window", None)
    with pytest.raises(desktop.DesktopUnavailable, match="window"):
        desktop.open_file()


def test_notify_reveal_and_links_use_argv(monkeypatch):
    monkeypatch.setenv("PYWEB_DESKTOP", "1")
    runs = []
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda argv, **kw: runs.append((argv, kw)))
    title = 'Done"; rm -rf ~ #'
    for plat in ("linux", "darwin", "win32"):
        monkeypatch.setattr(desktop.sys, "platform", plat)
        assert desktop.notify(title, "body $(x)") is True
        assert desktop.reveal("/tmp") is True
    linux, mac, win = runs[0][0], runs[2][0], runs[4]
    assert linux == ["notify-send", "--", title, "body $(x)"]
    assert mac[0] == "osascript" and '\\"' in mac[2]                               # quotes escaped for AppleScript
    assert win[0][0] == "powershell" and title not in " ".join(win[0]) and win[1]["env"]["PYWEB_TITLE"] == title
    assert runs[1][0] == ["xdg-open", "/tmp"] and runs[3][0][:2] == ["open", "-R"]
    assert runs[5][0][0] == "explorer"
    assert all(not kw.get("shell") for _, kw in runs)
    with pytest.raises(ValueError):
        desktop.open_url("file:///etc/passwd")


# ------------------------------------------------------------------ platform flags

def test_platform_flags(monkeypatch, app_file):
    monkeypatch.delenv("PYWEB_DESKTOP", raising=False)
    monkeypatch.delenv("PYWEB_TARGET", raising=False)
    assert platform.web and not platform.desktop and not platform.pwa and not platform.installed
    assert platform.server and not platform.mobile and not platform.edge and platform.target() == "web"
    monkeypatch.setenv("PYWEB_DESKTOP", "1")
    assert platform.desktop and platform.installed and not platform.web and platform.target() == "desktop"
    monkeypatch.delenv("PYWEB_DESKTOP")
    with pytest.raises(AttributeError):
        platform.tablet  # noqa: B018
    src = APP.replace('"desktop" if platform.desktop else "web"', '"installed" if platform.pwa else "tab"')
    c = TestClient(source=src)
    assert '<p id="where">tab</p>' in c.get("/notes").text
    c.cookies["pyweb_display"] = "standalone"
    assert '<p id="where">installed</p>' in c.get("/notes").text


def test_upgrade_points_at_placeholder_flags(tmp_path):
    from pyweb import upgrade
    path = tmp_path / "app.pyweb"
    path.write_text("from pyweb import App, platform\napp = App()\nif platform.mobile:\n    x = 1\n")
    found = [f for f in upgrade.check(str(path)) if f.code == "platform-placeholder"]
    assert found and "always False" in found[0].message


def test_window_settings():
    assert desktop.config(None) == desktop.Window()
    with pytest.raises(ValueError):
        desktop.Window(width=10)
    with pytest.raises(ValueError):
        desktop.Window(start_url="https://evil.example")
    with pytest.raises(TypeError):
        from pyweb import App
        App(desktop="big")


# ------------------------------------------------------------------ the shell, with a stand-in pywebview

def test_shell_wiring(app_file, tmp_path, monkeypatch, clean_env):
    made = {}
    mod = types.ModuleType("webview")
    mod.settings = {"OPEN_EXTERNAL_LINKS_IN_BROWSER": False, "ALLOW_FILE_URLS": True, "ALLOW_DOWNLOADS": False}
    mod.FileDialog = types.SimpleNamespace(OPEN=10, FOLDER=20, SAVE=30)

    class Win:
        def destroy(self):
            pass

    def create_window(title, url, **kw):
        made.update(title=title, url=url, **kw)
        return Win()

    def start(**kw):
        made["start"] = kw
        import urllib.request
        # While the window is "open": the launch URL lets it in, a plain request doesn't.
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        with opener.open(made["url"]) as r:
            made["first"] = (r.status, r.geturl(), r.read().decode())
        try:
            urllib.request.urlopen(made["url"].split("/__pyweb")[0] + "/notes")
        except urllib.error.HTTPError as exc:
            made["stranger"] = exc.code

    mod.create_window, mod.start = create_window, start
    monkeypatch.setitem(sys.modules, "webview", mod)
    monkeypatch.setenv("PYWEB_DESKTOP_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PYWEB_WORKER", "0")
    monkeypatch.chdir(tmp_path)
    shell.run(app_file)
    assert made["title"] == "Field Notes" and (made["width"], made["height"]) == (900, 600)
    assert made["url"].startswith("http://127.0.0.1:") and "/__pyweb/desktop/start?t=" in made["url"]
    assert made["start"]["private_mode"] is False and made["start"]["storage_path"].endswith("webview")
    assert mod.settings == {"OPEN_EXTERNAL_LINKS_IN_BROWSER": True, "ALLOW_FILE_URLS": False, "ALLOW_DOWNLOADS": True}
    status, url, body = made["first"]
    assert status == 200 and url.endswith("/notes") and '<p id="where">desktop</p>' in body
    assert made["stranger"] == 403
    assert desktop.SHELL.window is None                                           # closed with the window
    assert os.getcwd() == str(tmp_path / "data")


def test_missing_pywebview(app_file, tmp_path, monkeypatch, clean_env):
    monkeypatch.setitem(sys.modules, "webview", None)
    monkeypatch.setenv("PYWEB_DESKTOP_DATA", str(tmp_path / "d"))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="pyweb-stack\\[desktop\\]"):
        shell.run(app_file)


# ------------------------------------------------------------------ packaging

def test_icon_files():
    from pyweb import pwa
    pngs = {s: pwa.default_icon(s, "#336699") for s in (16, 32, 256)}
    ico = package.ico(pngs)
    reserved, kind, count = struct.unpack("<HHH", ico[:6])
    assert (reserved, kind, count) == (0, 1, 3)
    for i, size in enumerate(sorted(pngs)):
        w, h, _c, _r, _p, bpp, length, offset = struct.unpack("<BBBBHHII", ico[6 + 16 * i:22 + 16 * i])
        assert (w or 256) == size and bpp == 32
        assert pwa.decode_png(ico[offset:offset + length])[:2] == (size, size)
    icns = package.icns({16: pngs[16], 256: pngs[256]})
    assert icns[:4] == b"icns" and struct.unpack(">I", icns[4:8])[0] == len(icns)
    assert icns[8:12] == b"icp4" and b"ic08" in icns


def test_briefcase_project(app_file, tmp_path):
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "style.css").write_text("p{}")
    (tmp_path / "local.db").write_text("never shipped")
    out, name = package.make_project(app_file, str(tmp_path / "dist-desktop"), bundle="com.fieldnotes",
                                     version="1.2.0", author="Ann")
    assert name == "field_notes"
    tomllib = pytest.importorskip("tomllib")                                   # Python 3.11+
    conf = tomllib.load(open(os.path.join(out, "pyproject.toml"), "rb"))["tool"]["briefcase"]
    assert conf["project_name"] == "Field Notes" and conf["bundle"] == "com.fieldnotes" and conf["version"] == "1.2.0"
    app = conf["app"]["field_notes"]
    from pyweb import __version__
    assert app["requires"][0] == f"pyweb-stack[desktop]=={__version__}" and app["sources"] == ["src/field_notes"]
    assert "gir1.2-webkit2-4.1" in app["linux"]["system"]["debian"]["system_runtime_requires"]
    src = os.path.join(out, "src", "field_notes")
    assert os.path.isfile(os.path.join(src, "app", "app.pyweb"))
    assert os.path.isfile(os.path.join(src, "app", "static", "style.css"))
    assert not os.path.exists(os.path.join(src, "app", "local.db"))                # the developer's data stays home
    main = open(os.path.join(src, "__main__.py"), encoding="utf-8").read()
    assert "shell.run(" in main and "'app.pyweb'" in main
    compile(main, "__main__.py", "exec")
    assert open(os.path.join(out, "CHANGELOG"), encoding="utf-8").read().startswith("1.2.0")
    for f in ("field_notes-16.png", "field_notes-1024.png", "field_notes.ico", "field_notes.icns"):
        assert os.path.getsize(os.path.join(out, "icons", f)) > 0
    with pytest.raises(ValueError, match="bundle"):
        package.make_project(app_file, str(tmp_path / "x"), bundle="not a domain")
    with pytest.raises(ValueError, match="version"):
        package.make_project(app_file, str(tmp_path / "x"), version="one")
    assert not list(tmp_path.glob("x/src"))
