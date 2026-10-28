"""What ``pyweb dev`` serves: the current build of the app, the error overlay while it doesn't
compile, the dev tools (hot reload, inspector, AI help, timeline) for the developer, and the share
gate for everyone else. ``pyweb.cli`` runs it; the desktop shell reuses it for ``pyweb desktop --dev``.
"""

from __future__ import annotations

import json
import os
import sys


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _pretty_error(exc, path=None):
    from pyweb.cli import _pretty_error as pretty
    return pretty(exc, path)


def _dev_reload_js(names=("reload.js", "inspect.js", "assist.js", "timeline.js", "welcome.js")):
    """The dev scripts pages get: hot reload, and (for you, on this machine) the inspector, AI help,
    the timeline and the welcome panel."""
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime", "browser", "dev")
    out = []
    for name in names:
        with open(os.path.join(folder, name), encoding="utf-8") as fh:
            out.append("<script>" + fh.read().replace("</", "<\\/") + "</script>")
    return "".join(out)


DEV_RELOAD_JS = _dev_reload_js()
DEV_GUEST_JS = _dev_reload_js(("reload.js",))   # someone else (a share link, the LAN): hot reload only


_GUEST_ERROR = ("<!doctype html><meta charset=utf-8><title>Something went wrong</title>"
                "<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<style>body{font:16px/1.5 system-ui,sans-serif;margin:3rem auto;max-width:34rem;padding:0 1rem;"
                "color:#334155}</style><body><h1>Something went wrong</h1><p>The app hit an error. The details "
                "are on the developer's screen; this page reloads when they fix it.</p>")


def _error_overlay(exc, path, scripts=None):
    """HTML page shown in the browser while the app has a compile error."""
    import html as _h
    from pyweb.compiler.errors import describe
    try:
        source = _load(getattr(exc, "filename", None) or path)
    except OSError:
        source = None
    rec = describe(exc, source, path)
    where = rec.get("file") or path
    if rec.get("line"):
        where += f":{rec['line']}"
    parts = [f"<h1>{_h.escape(type(exc).__name__)}</h1>",
             f"<p class=where>{_h.escape(where)}</p><pre>{_h.escape(rec['message'])}</pre>"]
    if rec.get("frame"):
        parts.append(f"<pre class=code>{_h.escape(rec['frame'])}</pre>")
    if rec.get("hint"):
        parts.append(f"<p class=hint><b>Hint:</b> {_h.escape(rec['hint'])}</p>")
    if rec.get("docs"):
        parts.append(f'<p><a href="{_h.escape(rec["docs"])}" target=_blank rel=noopener>Read the docs</a></p>')
    return ("<!doctype html><meta charset=utf-8><title>PyWeb error</title>"
            "<style>body{font:15px/1.5 system-ui;margin:0;background:#1b1b1f;color:#eee}"
            "main{max-width:880px;margin:8vh auto;padding:0 24px}h1{color:#ff6b6b;font-size:20px}"
            "pre{background:#0f0f12;padding:16px;border-radius:8px;overflow:auto;font-size:13px}"
            ".code{border-left:3px solid #ff6b6b}.where{color:#aaa;margin:0}a{color:#8ab4ff}"
            ".hint{background:#26262c;padding:10px 14px;border-radius:8px}</style><main data-pw-error=compile>"
            + "".join(parts)
            + "<p>Fix the file and save: this page reloads automatically.</p></main>"
            + (DEV_RELOAD_JS if scripts is None else scripts))


def _local(client, headers):
    """A request from this machine, to a loopback address (not through a tunnel or the LAN)."""
    host = client[0] if isinstance(client, (tuple, list)) else (client or "127.0.0.1")
    name = next((v for k, v in (headers or {}).items() if k.lower() == "host"), "localhost")
    name = name.rsplit(":", 1)[0] if not name.startswith("[") else name[1:].split("]")[0]
    return host in ("127.0.0.1", "::1", "localhost") and name in ("127.0.0.1", "::1", "localhost")


def _welcome_marker(app_path):
    return os.path.join(os.path.dirname(os.path.abspath(app_path)), ".pyweb", "welcomed")


class _DevState:
    def __init__(self, path):
        self.path = path
        self.site = None
        self.error = None
        self.version = 0
        self.kind = "code"          # what the last change was: "code" (rebuilt) or "css" (stylesheets only)
        self.css = []               # the stylesheet URLs a "css" change touched
        self.welcome = False        # show the first-run welcome panel (pyweb dev sets it)
        self.port = None            # the port pyweb dev listens on (set when it's ready)


def _dev_load(state, Site):
    try:
        if state.site is None:
            state.site = Site(state.path, debug=True, dev=True)   # dev: elements know their source line
        else:
            state.site.reload()
        state.error = None
    except Exception as exc:  # noqa: BLE001 - shown in the browser overlay
        state.error = exc
    state.version += 1


def _note_for_ai(exc, req):
    """Remember a page's exception for "Fix with AI" on its error page."""
    from pyweb import assist
    assist.note_exception(exc, getattr(req, "id", None))


class _DevSite:
    """What the server serves while developing: the current build of the app, the error
    overlay while it doesn't compile, and a script that reloads pages after a change."""

    def __init__(self, state, link=None, share=None):
        self.state = state
        self.link = link
        self.share = share          # pyweb dev --share: the gate for requests through the tunnel
        from pyweb.runtime import server as _server
        if _note_for_ai not in _server.PAGE_EXCEPTION_HOOKS:
            _server.PAGE_EXCEPTION_HOOKS.append(_note_for_ai)

    @property
    def max_body(self):
        site = self.state.site
        return site.max_body if site is not None else 1_048_576

    def use_transport_timeouts(self):
        pass                       # no time limits while developing (a breakpoint can take a while)

    def time_limit(self, method, path):
        return None

    def timed_out(self, method, path, headers=None):
        return self.state.site.timed_out(method, path, headers)

    def _mine(self, client, headers):
        """The developer: a request from this machine to this dev server's loopback address. While
        sharing, Host must name the dev server's own port too, so a tunnel that rewrites Host to
        ``localhost`` still can't pose as the developer."""
        if not _local(client, headers):
            return False
        if self.share is not None and self.share.url and self.state.port:
            name = next((v for k, v in (headers or {}).items() if k.lower() == "host"), "")
            return name.rsplit(":", 1)[-1] == str(self.state.port) and ":" in name
        return True

    def _guest(self, client, headers):
        """Someone using a share link: the tunnel's public Host, or any other request the tunnel
        brought to us (it connects from this machine) that isn't the developer's."""
        if self.share is None or not self.share.url:
            return False
        if self.share.guest(headers):
            return True
        host = client[0] if isinstance(client, (tuple, list)) else (client or "127.0.0.1")
        return host in ("127.0.0.1", "::1", "localhost") and not self._mine(client, headers)

    def respond(self, method, path, headers, body=b"", client=None):
        state = self.state
        guest = self._guest(client, headers)
        if self.share is not None:
            refused = self.share.check(method, path, headers, guest=guest)
            if refused is not None:
                return refused
        bare = path.split("?")[0]
        mine = self._mine(client, headers)     # the developer, on this machine (not a share link or the LAN)
        if path.startswith("/__pyweb/dev/") and bare not in ("/__pyweb/dev/version", "/__pyweb/dev/changes") \
                and (guest or (not mine and not bare.startswith("/__pyweb/dev/agent/"))):
            from pyweb.telemetry.devtools import _json
            return _json({"error": "the dev tools are for the person running pyweb dev, on that machine"}, 403)
        if bare.endswith(".js.map") and not mine:
            # A source map holds the whole .pyweb file, server functions included: the developer's only.
            return 404, [("Content-Type", "text/plain"), ("Cache-Control", "no-store")], b"not found"
        if path == "/__pyweb/dev/version":
            return 200, [("Content-Type", "text/plain"), ("Cache-Control", "no-store")], str(state.version).encode()
        if path.split("?")[0] == "/__pyweb/dev/changes":
            body = json.dumps({"version": state.version, "kind": state.kind, "css": state.css,
                               "error": state.error is not None})
            return 200, [("Content-Type", "application/json"), ("Cache-Control", "no-store")], body.encode()
        if path.split("?")[0] == "/__pyweb/dev/open":
            return self._open(method, headers, body, client)
        if path.startswith("/__pyweb/dev/ai/"):
            return self._ai(method, path, headers, body, client)
        if path == "/__pyweb/dev/welcome":
            from pyweb.telemetry.devtools import _json, _same_origin
            if method != "POST" or not _same_origin(headers) or not self._mine(client, headers):
                return _json({"error": "POST from this page on this machine only"}, 403)
            state.welcome = False
            try:
                marker = _welcome_marker(state.path)
                os.makedirs(os.path.dirname(marker), exist_ok=True)
                with open(marker, "w", encoding="utf-8") as fh:
                    fh.write("shown\n")
            except OSError:
                pass
            return _json({"ok": True})
        if self.link is not None:
            answer = self.link.handle(method, path, headers, client, body=body)
            if answer is not None:
                return answer
        if state.error is not None or state.site is None:
            if not mine:                          # source lines and paths stay on the developer's screen
                return 500, [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store")], \
                    (_GUEST_ERROR + DEV_GUEST_JS).encode()
            overlay = _error_overlay(state.error, state.path, None)
            return 500, [("Content-Type", "text/html; charset=utf-8")], overlay.encode()
        from pyweb.telemetry import RECENT, devtools
        if path.startswith(devtools.PREFIX):
            loaded = getattr(state.site, "app", None)
            answer = devtools.handle(method, path, headers, body, app=getattr(loaded, "app", None),
                                     app_path=getattr(loaded, "path", None) or state.path)
            if answer is not None:
                return answer
        # No gzip on localhost: pages stay plain, so the reload script and the toolbar can be added.
        headers = {k: v for k, v in (headers or {}).items() if k.lower() != "accept-encoding"}
        status, hdrs, raw = state.site.respond(method, path, headers, body, client=client)
        ctype = next((v for k, v in hdrs if k.lower() == "content-type"), "")
        if status >= 500 and not mine and ctype.startswith("text/html"):
            # dev error pages show code and tracebacks: only for the person running pyweb dev
            hdrs = [(k, v) for k, v in hdrs if k.lower() not in ("content-length", "content-type")]
            hdrs.append(("Content-Type", "text/html; charset=utf-8"))
            raw = (_GUEST_ERROR + "</body>").encode()
        if ctype.startswith("text/html") and isinstance(raw, bytes) and b"</body>" in raw:
            dev_js = DEV_RELOAD_JS if mine else DEV_GUEST_JS
            if state.welcome and mine:
                info = json.dumps({"file": os.path.basename(state.path)}).replace("</", "<\\/")
                dev_js = f"<script>window.__PYWEB_WELCOME__={info};</script>" + dev_js
            raw = raw.replace(b"</body>", dev_js.encode() + b"</body>", 1)
            if mine and not os.environ.get("PYWEB_DEVTOOLS", "1").lower() in ("0", "false", "off"):
                rid = next((v for k, v in hdrs if k.lower() == "x-request-id"), None)
                summary = next((r for r in reversed(RECENT) if r["id"] == rid), None)
                raw = devtools.inject(raw, summary)
            hdrs = [(k, v) for k, v in hdrs if k.lower() != "content-length"]
        return status, hdrs, raw

    def _open(self, method, headers, body, client):
        """``POST /__pyweb/dev/open {"chain": ["file:line", ...]}``: open the source in your editor."""
        from pyweb import devopen
        from pyweb.telemetry.devtools import _json, _same_origin
        if method != "POST" or not _same_origin(headers) or not self._mine(client, headers):
            return _json({"error": "POST from this page on this machine only"}, 403)
        try:
            chain = json.loads(body or b"{}").get("chain")
        except (ValueError, AttributeError):
            chain = None
        if not isinstance(chain, list):
            return _json({"error": "send {\"chain\": [\"file:line\", ...]}"}, 400)
        app_dir = os.path.dirname(os.path.abspath(self.state.path))
        out = devopen.open_source(chain, app_dir)
        return _json(out, 404 if "file" not in out else 200)

    def _ai(self, method, path, headers, body, client):
        """``/__pyweb/dev/ai/{status,fix,edit,apply,undo}``: Fix with AI and Edit with AI (see pyweb.assist)."""
        from pyweb import assist, devopen
        from pyweb.telemetry.devtools import _json, _same_origin
        what = path.split("?")[0][len("/__pyweb/dev/ai/"):]
        if not self._mine(client, headers):
            return _json({"error": "AI help is for the person running pyweb dev, on this machine"}, 403)
        app_dir = os.path.dirname(os.path.abspath(self.state.path))
        if what == "status":
            return _json({"provider": assist.describe_provider(), "ready": assist.provider() is not None})
        if method != "POST" or not _same_origin(headers):
            return _json({"error": "POST from this page only"}, 403)
        if getattr(self.state, "proposals", None) is None:
            self.state.proposals = assist.Proposals(app_dir)    # shared by every connection
        self.proposals = self.state.proposals
        try:
            data = json.loads(body or b"{}")
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            return _json({"error": "send a JSON object"}, 400)
        try:
            if what == "fix":
                return _json(self._ai_fix(data))
            if what == "edit":
                instruction = str(data.get("instruction") or "").strip()[:2000]
                hit = devopen.pick(data.get("chain") if isinstance(data.get("chain"), list) else [], app_dir)
                if not instruction:
                    return _json({"error": "say what to change"}, 400)
                if hit is None or not hit[2]:
                    return _json({"error": "that element isn't from a file in this app"}, 400)
                file, line, _mine = hit
                text = _load(file)
                rel = os.path.relpath(file, app_dir)
                return _json(assist.ask_for("edit", file, rel, text, assist.edit_prompt(rel, text, line, instruction),
                                            self.proposals))
            if what == "apply":
                return _json(self.proposals.apply(str(data.get("id"))))
            if what == "undo":
                return _json(self.proposals.undo(str(data.get("id"))))
        except assist.AssistError as exc:
            return _json({"error": str(exc)}, 409)
        return _json({"error": f"unknown: {what}"}, 404)

    def _ai_fix(self, data):
        from pyweb import assist
        from pyweb.compiler.errors import pretty
        state = self.state
        if state.error is not None:
            exc = state.error
            file = getattr(exc, "filename", None) or state.path
            try:
                path = self.proposals.target(file)
            except assist.AssistError:
                path = self.proposals.target(state.path)
            text = _load(path)
            error = pretty(exc, text, path)
        else:
            found = assist.last_exception(data.get("request_id") or None) or assist.last_exception()
            if found is None:
                raise assist.AssistError("there's no error to fix right now")
            path = self.proposals.target(found["file"] or state.path)
            text = _load(path)
            error = found["text"]
        rel = os.path.relpath(path, self.proposals.app_dir)
        return assist.ask_for("fix", path, rel, text, assist.fix_prompt(rel, text, error), self.proposals)

    def websocket(self, path, headers, client=None):
        if self.share is not None and self.share.check("GET", path, headers,
                                                       guest=self._guest(client, headers)) is not None:
            return 403, "open the share link first"
        if self.state.site is None:
            return 503, "the app doesn't compile"
        return self.state.site.websocket(path, headers, client)


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
            time.sleep(0.1)
            now = snapshot()
            if now != last:
                changed = {p for p in set(now) | set(last) if now.get(p) != last.get(p)}
                last = now
                if changed and all(p.endswith(".css") for p in changed) and state.error is None:
                    # Stylesheets only: nothing to rebuild; the page swaps them in place.
                    state.css = ["/" + os.path.relpath(p, app_dir).replace(os.sep, "/") for p in sorted(changed)]
                    state.kind = "css"
                    state.version += 1
                    print(f"styles changed: {', '.join(state.css)}", flush=True)
                    continue
                state.kind, state.css = "code", []
                purge_local_modules()
                _dev_load(state, Site)
                if state.error is not None:
                    print("error: " + _pretty_error(state.error, state.path), flush=True)
                else:
                    print(f"reloaded {state.path}", flush=True)

    threading.Thread(target=poll, daemon=True, name="pyweb-reload").start()
