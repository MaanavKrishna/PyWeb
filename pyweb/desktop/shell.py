"""``pyweb desktop app.pyweb``: run the app in its own window.

The server runs on a background thread at ``127.0.0.1`` on a random port, behind
:class:`pyweb.desktop.gate.DesktopSite`; the window (pywebview: WebView2 on Windows,
WKWebView on macOS, WebKitGTK or Qt WebEngine on Linux) opens it. The app's database,
uploads and secrets live in its data folder (:func:`pyweb.desktop.data_dir`). Closing
the window stops the server.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import threading

from . import SHELL, config, data_dir, safe_name

SMOKE_OK = "pyweb desktop smoke: ok"


def app_name(app_path) -> str:
    """The app's name for its data folder and window: ``App(title=...)``, else its folder's name."""
    try:
        with open(app_path, encoding="utf-8") as fh:
            source = fh.read()
    except OSError:
        source = ""
    m = re.search(r"\bApp\s*\((?:[^()]|\([^()]*\))*?\btitle\s*=\s*(['\"])(.+?)\1", source, re.S)
    if m:
        return safe_name(m.group(2))
    folder = os.path.basename(os.path.dirname(os.path.abspath(app_path)))
    return safe_name(folder.replace("_", " ").replace("-", " ").title() or "PyWeb app")


def load_secrets(folder, environ=None) -> dict:
    """``PYWEB_AUTH_SECRET`` and ``PYWEB_DATA_KEY`` for this installation: made on first launch,
    kept in ``secrets.json`` (readable by the user only). Values already in the environment win."""
    env = os.environ if environ is None else environ
    path = os.path.join(folder, "secrets.json")
    stored = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                stored = json.load(fh)
        except (OSError, ValueError):
            stored = {}
    changed = False
    for name in ("PYWEB_AUTH_SECRET", "PYWEB_DATA_KEY"):
        if not isinstance(stored.get(name), str) or len(stored[name]) < 32:
            stored[name] = secrets.token_urlsafe(48)
            changed = True
    if changed:
        os.makedirs(folder, exist_ok=True)
        fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(stored, fh)
        os.replace(path + ".tmp", path)
    for name, value in stored.items():
        env.setdefault(name, value)
    return stored


def prepare(app_path, *, dev=False, environ=None):
    """Set up the environment for the desktop app (before it loads). Returns its data folder."""
    env = os.environ if environ is None else environ
    name = app_name(app_path)
    env["PYWEB_DESKTOP"] = "1"
    env.setdefault("PYWEB_DESKTOP_NAME", name)
    if not dev:
        env["PYWEB_ENV"] = "production"          # production error pages and security policy
    folder = data_dir(env["PYWEB_DESKTOP_NAME"], environ=env)
    os.makedirs(folder, exist_ok=True)
    load_secrets(folder, env)
    return folder


def _webview():
    try:
        import webview
    except ImportError:
        raise SystemExit('the desktop app needs pywebview: pip install "pyweb-stack[desktop]"') from None
    return webview


def build_site(app_path, *, dev=False):
    """The gated site for ``app_path`` (and a reload watcher with ``dev``)."""
    from .gate import DesktopSite
    if dev:
        from pyweb.devserver import _dev_load, _DevSite, _DevState
        from pyweb.hosting import Site
        state = _DevState(app_path)
        _dev_load(state, Site)
        inner, app = _DevSite(state), (state.site.app.app if state.site else None)
        inner.state_for_reload = state
    else:
        from pyweb.hosting import Site
        site = Site(os.path.abspath(app_path))
        inner, app = site, site.app.app
    cfg = config(app)
    return DesktopSite(inner, start_url=cfg.start_url), cfg, app


def main(app_path, *, dev=False, smoke=False, gui=None):
    """The desktop app's process (``pyweb desktop`` and packaged apps): open the window, and end the
    process when it closes. The server and jobs are stopped by then; a GUI toolkit's leftover threads
    (Qt WebEngine's, for one) must not keep an invisible app running."""
    code = 0
    try:
        run(app_path, dev=dev, smoke=smoke, gui=gui)
    except SystemExit as exc:
        if exc.code not in (None, 0):
            print(exc.code if isinstance(exc.code, str) else f"exit {exc.code}", file=sys.stderr)
            code = exc.code if isinstance(exc.code, int) else 1
    except BaseException:
        import traceback
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def run(app_path, *, dev=False, smoke=False, gui=None):
    """Open the app in its own window; returns when the window closes."""
    app_path = os.path.abspath(app_path)
    folder = prepare(app_path, dev=dev)
    os.chdir(folder)                     # sqlite:///app.db and uploads/ go to the data folder
    webview = _webview()
    gate, cfg, app = build_site(app_path, dev=dev)
    from pyweb.net.server import Running
    running = Running(gate, host="127.0.0.1", port=0)
    gate.bind(running.port)
    from pyweb import jobs
    try:
        jobs.start_embedded()
    except Exception as exc:  # noqa: BLE001 - the window matters more than background jobs
        print(f"background jobs not started: {exc}", file=sys.stderr)
    if dev:
        from pyweb.devserver import _watch_and_reload
        from pyweb.hosting import Site
        _watch_and_reload(gate.site.state_for_reload, Site)
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["ALLOW_DOWNLOADS"] = True
    title = cfg.title or getattr(app, "title", None) or os.environ.get("PYWEB_DESKTOP_NAME", "PyWeb")
    win = webview.create_window(title, gate.launch_url(), width=cfg.width, height=cfg.height,
                                min_size=tuple(cfg.min_size), resizable=cfg.resizable, maximized=cfg.maximized,
                                hidden=False)
    SHELL.window = win
    result = {}
    if smoke:
        def check():
            # The page reports itself (desktop.js): no script is run inside the page from outside.
            if not gate.ready.wait(60):
                result["error"] = "the app's page never appeared in the window"
            elif not gate.pages[-1]["root"]:
                result["error"] = "the window shows a page that isn't the app's"
            else:
                result["title"] = gate.pages[-1]["title"]
            win.destroy()
        threading.Thread(target=check, daemon=True, name="pyweb-desktop-smoke").start()
    icon = _icon_file(app_path, cfg, app, folder)
    try:
        webview.start(gui=gui, private_mode=False, storage_path=os.path.join(folder, "webview"), icon=icon)
    finally:
        SHELL.window = None
        running.stop()
        try:
            jobs.stop_all(timeout=5)
        except Exception:  # noqa: BLE001
            pass
    if smoke:
        if "error" in result:
            raise SystemExit(f"pyweb desktop smoke: failed: {result['error']}")
        print(f"{SMOKE_OK} {result.get('title', '')}", flush=True)
    return result


def _icon_file(app_path, cfg, app, folder):
    """A PNG for the window icon (GTK and Qt use it): the app's own, or a drawn one."""
    from pyweb import pwa
    try:
        pcfg = pwa.config(app) or pwa.PWA()
        if cfg.icon:
            pcfg = pwa.PWA(icon=cfg.icon, theme_color=pcfg.theme_color)
        data = pwa.icon_png(pcfg, os.path.dirname(app_path), 256)
        path = os.path.join(folder, "icon.png")
        with open(path, "wb") as fh:
            fh.write(data)
        return path
    except Exception:  # noqa: BLE001 - no icon is better than no window
        return None
