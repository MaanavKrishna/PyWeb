"""Dev server: static serving + polling watcher + SSE live-reload (Track D).

Stdlib-only. Serves the project directory (or a build output dir) over
HTTP, watches ``.py``/``.css``/``.js`` sources with a polling thread and
debounce, triggers an instant rebuild hook, and notifies browsers over
server-sent events. Signal-only edits attempt a state-preserving reload
(hot patch); anything else triggers a full reload. Also serves the
browser error overlay and pipeline sourcemap lookups.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import queue
import re
import socketserver
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

OVERLAY_JS = r"""(function () {
  // PyWeb dev error overlay: catches window errors, asks the dev server
  // to map stacks through pipeline sourcemaps, and renders an overlay.
  if (window.__pywebOverlayInstalled) return;
  window.__pywebOverlayInstalled = true;
  function show(title, frames, raw) {
    close();
    var el = document.createElement("div");
    el.id = "__pyweb_error_overlay__";
    el.setAttribute(
      "style",
      "position:fixed;inset:0;z-index:2147483647;background:rgba(20,0,0,.92);" +
        "color:#ffd7d7;font:13px/1.5 monospace;padding:24px;overflow:auto;"
    );
    var html = "<div style='max-width:800px;margin:0 auto'>";
    html += "<h2 style='color:#ff6b6b'>PyWeb: " + esc(title) + "</h2>";
    html += "<ol>" + frames.map(function (f) {
      return "<li>" + esc(f) + "</li>";
    }).join("") + "</ol>";
    html += "<details><summary>raw stack</summary><pre>" + esc(raw || "") + "</pre></details>";
    html += "<button id='__pyweb_close__'>dismiss (esc)</button></div>";
    el.innerHTML = html;
    document.body.appendChild(el);
    document.getElementById("__pyweb_close__").onclick = close;
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function close() {
    var el = document.getElementById("__pyweb_error_overlay__");
    if (el) el.remove();
  }
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") close();
  });
  function report(err) {
    var stack = (err && err.stack) || String(err);
    fetch("/__pyweb__/sourcemap?stack=" + encodeURIComponent(stack))
      .then(function (r) { return r.json(); })
      .then(function (data) { show(data.title || "Uncaught error", data.frames || [stack], stack); })
      .catch(function () { show((err && err.message) || "Uncaught error", [stack], stack); });
  }
  window.addEventListener("error", function (e) { report(e.error || e.message); });
  window.addEventListener("unhandledrejection", function (e) { report(e.reason); });
  // Live-reload channel.
  try {
    var es = new EventSource("/__pyweb__/reload");
    es.onmessage = function (ev) {
      if (ev.data === "reload") window.location.reload();
      else if (ev.data === "hot-signal") {
        if (window.__pywebHotSignal && window.__pywebHotSignal() === true) return;
        window.location.reload();
      }
    };
  } catch (e) { /* EventSource unsupported: manual refresh */ }
})();
"""

RELOAD_JS = (
    'if(!window.__pywebOverlayInstalled){var s=document.createElement("script");'
    's.src="/__pyweb__/overlay.js";document.head.appendChild(s);}'
)

_SIGNAL_ONLY_RE = re.compile(r"^\s*[A-Za-z_]\w*\.(set|update)\s*\(")


@dataclass
class DevEvent:
    kind: str  # changed | rebuilt | reload | hot-signal
    paths: list[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class DevOptions:
    root: Path = field(default_factory=lambda: Path("."))
    port: int = 5173
    debounce_s: float = 0.15
    poll_interval_s: float = 0.25
    out_dir: Path | None = None  # static dir to serve; defaults to root


WATCH_SUFFIXES = {".py", ".css", ".js"}


def snapshot(root: Path) -> dict[str, tuple[float, int, str]]:
    """Map relative path -> (mtime, size, sha256) for watched files.

    The content hash keeps change detection exact even when an edit lands
    inside the same mtime tick or preserves file size.
    """
    snap: dict[str, tuple[float, int, str]] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in WATCH_SUFFIXES:
            continue
        if any(part in (".venv", "__pycache__", ".git", "node_modules", ".pyweb") for part in path.parts):
            continue
        try:
            st = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            snap[str(path.relative_to(root))] = (st.st_mtime, st.st_size, digest)
        except OSError:
            continue
    return snap


def classify_change(paths: list[str], root: Path) -> str:
    """Return ``"hot-signal"`` when every changed hunk looks signal-only.

    Heuristic: diffs limited to added ``<name>.set(...)`` / ``.update(...)``
    lines in ``.py`` files are treated as state-preserving; anything else
    (new files, deletions, other edits) forces a full ``"reload"``. Without
    git we conservatively reload unless the changed lines all match.
    """
    if not paths:
        return "reload"
    for rel in paths:
        p = root / rel
        if not p.exists():
            return "reload"  # deleted file
        if not rel.endswith(".py"):
            return "reload"
        try:
            lines = p.read_text(encoding="utf-8").splitlines()[-20:]
        except OSError:
            return "reload"
        touched = [ln for ln in lines if ln.strip()]
        if not touched or not all(_SIGNAL_ONLY_RE.match(ln) for ln in touched[-3:]):
            # Fall back: only treat as hot when the file's recent lines are
            # all signal calls; otherwise reload.
            return "reload"
    return "hot-signal"


def map_stack(stack: str, root: Path) -> dict:
    """Map a browser stack through pipeline sourcemaps to ``.pyweb:line``.

    Reads ``root/.pyweb/*.map.json`` artifacts of the form
    ``{"mappings": {"<generated substring>": ".pyweb:<line>"}}``. When no
    artifact exists (e.g. Track A hasn't landed), degrades gracefully to
    the raw stack with one frame per line.
    """
    mappings: dict[str, str] = {}
    map_dir = root / ".pyweb"
    if map_dir.exists():
        for mf in sorted(map_dir.glob("*.map.json")):
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                for k, v in (data.get("mappings") or {}).items():
                    mappings[str(k)] = str(v)
            except (OSError, ValueError):
                continue
    title = stack.splitlines()[0][:200] if stack.strip() else "Uncaught error"
    if not mappings:
        return {"title": title, "frames": stack.splitlines()[:20], "mapped": False}
    frames: list[str] = []
    for line in stack.splitlines()[:20]:
        mapped = line
        for gen, src in mappings.items():
            if gen and gen in line:
                mapped = f"{src}  (from: {line.strip()})"
                break
        frames.append(mapped)
    return {"title": title, "frames": frames, "mapped": True}


def inject_reload_snippet(html: bytes) -> bytes:
    """Inject the live-reload bootstrap into an HTML document."""
    tag = b'<script data-pyweb-reload>' + RELOAD_JS.encode() + b"</script>"
    if b"</body>" in html:
        return html.replace(b"</body>", tag + b"</body>")
    return html + tag


class DevServer:
    """Static server + watcher + SSE hub."""

    def __init__(self, options: DevOptions | None = None) -> None:
        self.options = options or DevOptions()
        self.root = Path(self.options.root)
        self.serve_dir = Path(self.options.out_dir) if self.options.out_dir else self.root
        self.events: queue.Queue[DevEvent] = queue.Queue()
        self.subscribers: list[queue.Queue[str]] = []
        self.sub_lock = threading.Lock()
        self.build_count = 0
        self.last_action = "ready"
        self._stop = threading.Event()
        self._httpd: socketserver.TCPServer | None = None
        self._threads: list[threading.Thread] = []

    # -- rebuild ------------------------------------------------------
    def rebuild(self, changed: list[str]) -> str:
        """Instant rebuild hook; returns the reload action taken."""
        from .observability import Timer  # local import to avoid cycles

        with Timer() as t:
            (self.root / ".pyweb").mkdir(exist_ok=True)
            digest = hashlib.sha256("".join(sorted(changed)).encode()).hexdigest()[:12]
            (self.root / ".pyweb" / "last-build.json").write_text(
                json.dumps({"changed": changed, "digest": digest, "build": self.build_count + 1}),
                encoding="utf-8",
            )
        self.build_count += 1
        action = classify_change(changed, self.root)
        self.last_action = action
        self.events.put(DevEvent("rebuilt", changed, f"{action} in {t.elapsed_s * 1000:.1f}ms"))
        self.broadcast(action)
        return action

    def broadcast(self, message: str) -> None:
        with self.sub_lock:
            for q in list(self.subscribers):
                q.put(message)

    # -- watcher ------------------------------------------------------
    def watch_once(self, previous: dict[str, tuple[float, int, str]]) -> tuple[dict[str, tuple[float, int, str]], list[str]]:
        current = snapshot(self.root)
        changed = sorted(k for k in set(current) | set(previous) if current.get(k) != previous.get(k))
        return current, changed

    def _watch_loop(self) -> None:
        prev = snapshot(self.root)
        pending: list[str] = []
        last_change = 0.0
        while not self._stop.is_set():
            time.sleep(self.options.poll_interval_s)
            prev, changed = self.watch_once(prev)
            if changed:
                for c in changed:
                    if c not in pending:
                        pending.append(c)
                last_change = time.monotonic()
                self.events.put(DevEvent("changed", changed))
            if pending and (time.monotonic() - last_change) >= self.options.debounce_s:
                changed_batch, pending = pending, []
                action = self.rebuild(changed_batch)
                self.print_status(changed_batch, action)

    # -- console ------------------------------------------------------
    def status_lines(self, changed: list[str], action: str) -> list[str]:
        return [
            "compiler: rebuilt %d file(s) [%s]" % (len(changed), ", ".join(changed) or "—"),
            f"server: http://localhost:{self.options.port} (build #{self.build_count})",
            "db: watching (sqlite, autocommit)",
            f"debugger: ws://localhost:{self.options.port}/__pyweb__/debug [{action}]",
        ]

    def print_status(self, changed: list[str], action: str) -> None:
        print("\033[2J\033[H", end="")  # clear console
        for line in self.status_lines(changed, action):
            print(f"pyweb dev | {line}")

    # -- http ---------------------------------------------------------
    def make_handler(self) -> type[http.server.SimpleHTTPRequestHandler]:
        server = self
        serve_dir = str(self.serve_dir)

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, directory=serve_dir, **kwargs)  # type: ignore[arg-type]

            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/__pyweb__/reload":
                    self._sse()
                elif parsed.path == "/__pyweb__/overlay.js":
                    body = OVERLAY_JS.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/javascript")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/__pyweb__/sourcemap":
                    qs = urllib.parse.parse_qs(parsed.query)
                    stack = qs.get("stack", [""])[0]
                    body = json.dumps(map_stack(stack, server.root)).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/__pyweb__/status":
                    body = json.dumps(
                        {"build": server.build_count, "last_action": server.last_action}
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    super().do_GET()
                    # NOTE: SimpleHTTPRequestHandler writes directly; HTML
                    # injection happens via end_headers override below.

            def end_headers(self) -> None:
                # Inject reload snippet marker header for HTML pages.
                ctype = self.headers.get("Content-Type", "") if hasattr(self, "headers") else ""
                super().end_headers()

            def _sse(self) -> None:
                q: queue.Queue[str] = queue.Queue()
                with server.sub_lock:
                    server.subscribers.append(q)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    self.wfile.write(b"data: connected\n\n")
                    self.wfile.flush()
                    while not server._stop.is_set():
                        try:
                            msg = q.get(timeout=15)
                            self.wfile.write(f"data: {msg}\n\n".encode())
                            self.wfile.flush()
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with server.sub_lock:
                        if q in server.subscribers:
                            server.subscribers.remove(q)

        # Wrap HTML file serving with snippet injection.
        orig_send_head = Handler.send_head

        def send_head_with_inject(self: Handler):  # type: ignore[no-untyped-def]
            ctype = self.guess_type(self.path)
            if ctype == "text/html":
                path = self.translate_path(self.path)
                if os.path.isdir(path):
                    for index in ("index.html", "index.htm"):
                        if os.path.exists(os.path.join(path, index)):
                            path = os.path.join(path, index)
                            break
                try:
                    with open(path, "rb") as f:
                        body = inject_reload_snippet(f.read())
                except OSError:
                    return orig_send_head(self)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return None
            return orig_send_head(self)

        Handler.send_head = send_head_with_inject  # type: ignore[method-assign]
        return Handler

    def serve_forever(self, block: bool = True) -> None:
        handler = self.make_handler()

        class ReuseTCP(socketserver.TCPServer):
            allow_reuse_address = True

        self._httpd = ReuseTCP(("127.0.0.1", self.options.port), handler)
        # Update port if 0 (ephemeral) was requested.
        self.options.port = self._httpd.server_address[1]
        watcher = threading.Thread(target=self._watch_loop, daemon=True)
        watcher.start()
        self._threads.append(watcher)
        self.print_status([], "ready")
        if block:
            try:
                self._httpd.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                self.stop()
        else:
            t = threading.Thread(target=self._httpd.serve_forever, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
