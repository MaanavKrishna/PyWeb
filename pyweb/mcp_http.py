"""``pyweb mcp --http``: the MCP server over Streamable HTTP (one endpoint, default ``/mcp``).

For clients that connect by URL instead of starting a process. A POST carries one JSON-RPC
message; requests get a JSON response, or an event stream (progress and log notifications, then
the response) when the client accepts ``text/event-stream``. ``initialize`` starts a session
(``Mcp-Session-Id``); ``DELETE`` ends it.

It listens on 127.0.0.1 by default and refuses requests whose ``Origin`` or ``Host`` isn't local
(a web page you visit can't reach it through DNS rebinding). It always needs a token, sent as
``Authorization: Bearer <token>``: ``--token``, ``PYWEB_MCP_TOKEN``, or else one it makes and keeps in
``.pyweb/mcp-token`` (readable only by you), so other users and programs on the machine can't drive
your project's tools.
"""

from __future__ import annotations

import contextvars
import hmac
import http.server
import json
import os
import secrets
import socketserver
import threading
import urllib.parse

from . import mcp as M

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")
MAX_BODY = 4 * 1024 * 1024
_EMIT = contextvars.ContextVar("pyweb_mcp_emit", default=None)


class Sessions:
    def __init__(self):
        self.servers = {}
        self.lock = threading.Lock()

    def new(self):
        sid = secrets.token_urlsafe(24)
        server = M.Server(send=_emit)
        with self.lock:
            self.servers[sid] = server
            while len(self.servers) > 64:                  # forget the oldest idle sessions
                self.servers.pop(next(iter(self.servers)))
        return sid, server

    def get(self, sid):
        with self.lock:
            return self.servers.get(sid)

    def end(self, sid):
        with self.lock:
            return self.servers.pop(sid, None) is not None


def _emit(msg):
    """Send a notification on the event stream of the request being handled (dropped for JSON replies)."""
    emit = _EMIT.get()
    if emit is not None:
        emit(msg)


def make_handler(sessions, *, path="/mcp", token=None, allowed_hosts=LOCAL_HOSTS):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "pyweb-mcp"

        def log_message(self, fmt, *args):            # quiet: stdout may be someone's terminal
            pass

        # -- checks ---------------------------------------------------------------------
        def _refuse(self):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
            if host not in allowed_hosts and "*" not in allowed_hosts:
                return 403, "Host not allowed"
            origin = self.headers.get("Origin")
            if origin:
                o = urllib.parse.urlsplit(origin).hostname or ""
                if o not in [h.strip("[]") for h in LOCAL_HOSTS] and o not in allowed_hosts:
                    return 403, "Origin not allowed"
            if token:
                sent = self.headers.get("Authorization", "")
                if not hmac.compare_digest(sent, f"Bearer {token}"):
                    return 401, "missing or wrong bearer token"
            if urllib.parse.urlsplit(self.path).path != path:
                return 404, f"the MCP endpoint is {path}"
            return None

        def _plain(self, status, text, headers=()):
            body = text.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, data, headers=()):
            body = json.dumps(data, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        # -- methods --------------------------------------------------------------------
        def do_GET(self):
            refused = self._refuse()
            if refused:
                return self._plain(*refused)
            self._plain(405, "this server sends messages only in answer to a POST", [("Allow", "POST, DELETE")])

        def do_DELETE(self):
            refused = self._refuse()
            if refused:
                return self._plain(*refused)
            sid = self.headers.get("Mcp-Session-Id")
            if not sid or not sessions.end(sid):
                return self._plain(404, "no such session")
            self._plain(200, "session ended")

        def do_POST(self):
            refused = self._refuse()
            if refused:
                return self._plain(*refused)
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._plain(413, "message too large")
            try:
                msg = json.loads(self.rfile.read(length) or b"null")
            except ValueError:
                return self._json(400, M._error(None, -32700, "parse error"))
            msgs = msg if isinstance(msg, list) else [msg]
            is_init = any(isinstance(m, dict) and m.get("method") == "initialize" for m in msgs)
            sid = self.headers.get("Mcp-Session-Id")
            extra = []
            if is_init:
                sid, server = sessions.new()
                extra.append(("Mcp-Session-Id", sid))
            else:
                if not sid:
                    return self._plain(400, "missing Mcp-Session-Id (send initialize first)")
                server = sessions.get(sid)
                if server is None:
                    return self._plain(404, "unknown or ended session: initialize again")
                version = self.headers.get("MCP-Protocol-Version")
                if version and version not in M.PROTOCOL_VERSIONS:
                    return self._plain(400, f"unsupported MCP-Protocol-Version {version}")
            requests = [m for m in msgs if isinstance(m, dict) and "id" in m and "method" in m]
            if not requests:                               # notifications and responses
                for m in msgs:
                    server.handle(m)
                self.send_response(202)
                self.send_header("Content-Length", "0")
                for k, v in extra:
                    self.send_header(k, v)
                self.end_headers()
                return
            accept = self.headers.get("Accept", "")
            stream = "text/event-stream" in accept and any(m.get("method") == "tools/call" for m in requests)
            if not stream:
                answers = [r for r in (server.handle(m) for m in msgs) if r is not None]
                return self._json(200, answers if isinstance(msg, list) else answers[0], extra)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.close_connection = True
            lock = threading.Lock()

            def write(event):
                data = json.dumps(event, default=str)
                with lock:
                    self.wfile.write(f"event: message\ndata: {data}\n\n".encode())
                    self.wfile.flush()
            reset = _EMIT.set(write)
            try:
                for m in msgs:
                    answer = server.handle(m)
                    if answer is not None:
                        write(answer)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                _EMIT.reset(reset)

    return Handler


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(host="127.0.0.1", port=8765, *, path="/mcp", token=None):
    """The HTTP server (not started). Refuses to listen beyond this machine without a token."""
    token = token or os.environ.get("PYWEB_MCP_TOKEN") or None
    if host not in LOCAL_HOSTS and not token:
        raise SystemExit("pyweb mcp --http on a non-local address needs --token (or PYWEB_MCP_TOKEN)")
    allowed = LOCAL_HOSTS if host in LOCAL_HOSTS else (*LOCAL_HOSTS, host.lower(), "*")
    srv = _Server((host, port), make_handler(Sessions(), path=path, token=token, allowed_hosts=allowed))
    return srv


def local_token(folder="."):
    """The token in ``<folder>/.pyweb/mcp-token``, made (0600) the first time."""
    d = os.path.join(folder, ".pyweb")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "mcp-token")
    try:
        st = os.stat(p)
        if hasattr(os, "getuid") and (st.st_uid != os.getuid() or st.st_mode & 0o077):
            raise SystemExit(f"{p} is readable by others or not yours; delete it and run again")
        with open(p) as f:
            tok = f.read().strip()
        if len(tok) >= 20:
            return tok
    except FileNotFoundError:
        pass
    tok = secrets.token_urlsafe(32)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    return tok


def serve_http(host="127.0.0.1", port=8765, *, path="/mcp", token=None):
    import sys
    made = None
    if not (token or os.environ.get("PYWEB_MCP_TOKEN")):
        token = made = local_token()
    srv = make_server(host, port, path=path, token=token)
    print(f"PyWeb MCP server: http://{host}:{srv.server_address[1]}{path}", file=sys.stderr, flush=True)
    if made:
        print(f"  send the header  Authorization: Bearer {made}\n  (kept in .pyweb/mcp-token)",
              file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
