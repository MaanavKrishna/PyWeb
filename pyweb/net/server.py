"""PyWeb's server: HTTP/1.1, WebSockets and Server-Sent Events on one asyncio loop.

Standard library only. Thousands of idle keep-alive connections, event
streams and sockets cost a coroutine each, not a thread. Page renders and
RPC calls are ordinary blocking Python, so they run on a thread pool
(``PYWEB_THREADS``, default 32) and never stall the loop.

Hardening:

* a strict parser (:mod:`pyweb.net.http`) that refuses ambiguous framing;
* time limits on reading a request head (slowloris), each body read, idle
  keep-alive, and each write (clients that stop reading);
* body limits enforced before reading (413, with ``Expect: 100-continue``
  answered only when the body is acceptable);
* at most ``PYWEB_MAX_CONNECTIONS`` connections; beyond that a 503;
* graceful drain: stop accepting, finish in-flight requests, close idle
  connections, tell sockets and streams to reconnect elsewhere.

``serve(site, port=8000, workers=4)`` forks workers that share the listening
socket; a worker that dies is replaced.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import signal
import socket
import time

from . import http as H
from . import ws as W

HEAD_TIMEOUT = 10.0           # seconds to send a whole request head
IDLE_TIMEOUT = 15.0           # seconds a keep-alive connection may sit idle
WS_PATH = "/__pyweb/ws"


class Server:
    def __init__(self, site, *, threads=None, max_connections=None, socket_timeout=None, logger=None):
        from pyweb.config import settings
        conf = settings()
        self.site = site
        self.logger = logger
        self.max_connections = max_connections or conf.max_connections
        self.timeout = float(socket_timeout or conf.socket_timeout)
        self.pool = concurrent.futures.ThreadPoolExecutor(threads or conf.threads, thread_name_prefix="pyweb")
        self.active = 0
        self.busy = 0                      # connections in the middle of a request
        self.draining = False
        self.servers = []
        self.idle_writers = set()
        self.writers = set()
        self.started = time.time()

    # ------------------------------------------------------------- lifecycle
    async def start(self, host="127.0.0.1", port=8000, *, sock=None):
        kw = {"limit": H.MAX_HEAD + 1024}
        if sock is not None:
            srv = await asyncio.start_server(self._connection, sock=sock, **kw)
        else:
            srv = await asyncio.start_server(self._connection, host, port, reuse_address=True, **kw)
        self.servers.append(srv)
        return srv

    @property
    def port(self):
        return self.servers[0].sockets[0].getsockname()[1]

    async def drain(self, timeout=25.0):
        """Stop accepting, let in-flight requests finish (up to ``timeout``), then close everything."""
        self.draining = True
        for srv in self.servers:
            srv.close()
        loop = asyncio.get_running_loop()
        from pyweb.hosting import shutdown
        await loop.run_in_executor(None, shutdown, min(timeout, 10.0), self.logger)
        for writer in list(self.idle_writers):
            writer.close()
        end = time.monotonic() + timeout
        while self.busy and time.monotonic() < end:
            await asyncio.sleep(0.05)
        for writer in list(self.writers):                  # streams and sockets still open
            writer.close()
        end = time.monotonic() + 2.0
        while self.active and time.monotonic() < end:
            await asyncio.sleep(0.02)
        self.pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ connections
    async def _connection(self, reader, writer):
        if self.active >= self.max_connections or self.draining:
            try:
                writer.write(_simple(503, "server busy", close=True, extra=[("Retry-After", "1")]))
                await asyncio.wait_for(writer.drain(), 2)
            except (OSError, asyncio.TimeoutError):
                pass
            await _linger_close(reader, writer)
            return
        self.active += 1
        self.writers.add(writer)
        peer = writer.get_extra_info("peername")
        client = peer[0] if isinstance(peer, tuple) else None
        try:
            await self._serve(reader, writer, client)
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            pass
        except Exception as exc:  # noqa: BLE001 - one connection's bug must not take the server down
            if self.logger is not None:
                self.logger.info(f"connection error: {type(exc).__name__}: {exc}")
        finally:
            self.active -= 1
            self.idle_writers.discard(writer)
            self.writers.discard(writer)
            await _linger_close(reader, writer)

    async def _serve(self, reader, writer, client):
        first = True
        while not self.draining:
            # Wait for the next request: a short idle window between keep-alive requests.
            self.idle_writers.add(writer)
            try:
                head_bytes = await asyncio.wait_for(self._read_head(reader), HEAD_TIMEOUT if first else IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                if first:
                    await self._send_error(writer, 408, "request head not received in time")
                return
            except H.HTTPError as exc:
                await self._send_error(writer, exc.status, exc.message)
                return
            finally:
                self.idle_writers.discard(writer)
            if head_bytes is None:
                return                                      # the client closed the connection
            first = False
            self.busy += 1
            try:
                keep = await self._request(reader, writer, client, head_bytes)
            finally:
                self.busy -= 1
            if not keep:
                return

    async def _read_head(self, reader):
        try:
            data = await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError as exc:
            if not exc.partial.strip():
                return None
            raise H.HTTPError(400, "connection closed mid-request") from None
        except asyncio.LimitOverrunError:
            raise H.HTTPError(431, "request head too large") from None
        while data.startswith(b"\r\n"):                    # tolerate stray CRLF between requests (RFC 9112 2.2)
            data = data[2:]
        if not data:
            return await self._read_head(reader)
        return data

    async def _request(self, reader, writer, client, head_bytes):
        try:
            head = H.parse_head(head_bytes)
        except H.HTTPError as exc:
            await self._send_error(writer, exc.status, exc.message)
            return False
        path = head.target
        if head.upgrade and "websocket" in head.headers.get("upgrade", "").lower():
            await self._websocket(reader, writer, client, head)
            return False
        from pyweb.hosting import body_limit
        limit = body_limit(path.split("?")[0], self.site.max_body)
        if head.length > limit:
            await self._send_error(writer, 413, "request body too large")
            return False
        if head.expect_continue and (head.length or head.chunked):
            writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
        try:
            body = await self._read_body(reader, head, limit)
        except asyncio.TimeoutError:
            await self._send_error(writer, 408, "request body not received in time")
            return False
        except H.HTTPError as exc:
            await self._send_error(writer, exc.status, exc.message)
            return False
        except asyncio.IncompleteReadError:
            return False
        headers = head.header_dict()
        loop = asyncio.get_running_loop()
        try:
            status, hdrs, out = await loop.run_in_executor(
                self.pool, self.site.respond, head.method, path, headers, body, client)
        except Exception as exc:  # noqa: BLE001
            if self.logger is not None:
                self.logger.info(f"request failed: {type(exc).__name__}: {exc}")
            await self._send_error(writer, 500, "internal error")
            return False
        keep = head.keep_alive and not self.draining
        if hasattr(out, "aiter"):
            await self._stream(reader, writer, head, status, hdrs, out)
            return False
        hdrs = [(k, v) for k, v in hdrs if k.lower() not in ("content-length", "connection", "transfer-encoding")]
        hdrs.append(("Content-Length", str(len(out))))
        if not keep:
            hdrs.append(("Connection", "close"))
        elif head.version == "HTTP/1.0":
            hdrs.append(("Connection", "keep-alive"))
        await self._write(writer, H.response_head(status, hdrs) + (b"" if head.method == "HEAD" else out))
        return keep

    async def _read_body(self, reader, head, limit):
        if head.chunked:
            # Read exactly the chunked body (size lines, data, trailers) so a pipelined
            # request after it stays in the reader untouched.
            decoder = H.ChunkedDecoder(limit)
            parts = []
            while not decoder.done:
                if decoder.state == "data":
                    piece = await asyncio.wait_for(reader.readexactly(decoder.left), self.timeout)
                else:
                    try:
                        piece = await asyncio.wait_for(reader.readuntil(b"\r\n"), self.timeout)
                    except asyncio.LimitOverrunError:
                        raise H.HTTPError(400, "chunk line too long") from None
                parts.append(decoder.feed(piece))
            return b"".join(parts)
        if not head.length:
            return b""
        return await asyncio.wait_for(reader.readexactly(head.length), max(self.timeout, head.length / 65536))

    async def _write(self, writer, data):
        writer.write(data)
        await asyncio.wait_for(writer.drain(), self.timeout)

    async def _send_error(self, writer, status, message):
        try:
            await self._write(writer, _simple(status, message, close=True))
        except (OSError, asyncio.TimeoutError):
            pass

    # --------------------------------------------------------------- streams
    async def _stream(self, reader, writer, head, status, hdrs, body):
        """Event streams and streamed RPC results: chunked, until the end or the client leaves."""
        hdrs = [(k, v) for k, v in hdrs if k.lower() not in ("content-length", "connection", "transfer-encoding")]
        chunked = head.version == "HTTP/1.1"
        hdrs += [("Transfer-Encoding", "chunked")] if chunked else []
        hdrs.append(("Connection", "close"))
        gone = asyncio.ensure_future(_wait_closed(reader))
        try:
            await self._write(writer, H.response_head(status, hdrs))
            if head.method == "HEAD":
                return
            async for chunk in body.aiter():
                if gone.done() or self.draining:
                    break
                if chunk:
                    await self._write(writer, b"%x\r\n%s\r\n" % (len(chunk), chunk) if chunked else chunk)
            if chunked:
                await self._write(writer, b"0\r\n\r\n")
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            gone.cancel()
            body.close()

    # ------------------------------------------------------------ websockets
    async def _websocket(self, reader, writer, client, head):
        problem = W.handshake_problem(head.method, head.headers)
        if problem:
            await self._send_error(writer, 400, problem)
            return
        if head.target.split("?")[0] != WS_PATH:
            await self._send_error(writer, 404, "no WebSocket here")
            return
        loop = asyncio.get_running_loop()
        live = await loop.run_in_executor(self.pool, self.site.websocket, head.target, head.header_dict(), client)
        if isinstance(live, tuple):
            await self._send_error(writer, *live)
            return
        await self._write(writer, H.response_head(101, [
            ("Upgrade", "websocket"), ("Connection", "Upgrade"),
            ("Sec-WebSocket-Accept", W.accept_key(head.headers["sec-websocket-key"]))]))
        conn = _WSConnection(reader, writer, self.timeout)
        await live.run(conn.recv, conn.send, conn.close)
        await conn.close(W.NORMAL, "")


class _WSConnection:
    """Frames over a stream: answers pings, assembles messages, closes politely."""

    def __init__(self, reader, writer, timeout):
        self.reader, self.writer, self.timeout = reader, writer, timeout
        self.parser = W.Parser(max_message=128 * 1024)     # the live hub accepts at most 64 KB anyway
        self.pending = []
        self.closed = False
        self.lock = asyncio.Lock()

    async def recv(self):
        while not self.pending:
            if self.closed:
                return None
            try:
                data = await self.reader.read(65536)
            except (ConnectionError, OSError):
                data = b""
            if not data:
                self.closed = True
                return None
            try:
                events = self.parser.feed(data)
            except W.ProtocolError as exc:
                await self.close(exc.code, exc.reason)
                return None
            for event in events:
                if isinstance(event, W.Message):
                    self.pending.append(event.data)
                elif isinstance(event, W.Ping):
                    await self._raw(W.frame(W.PONG, event.data))
                elif isinstance(event, W.Closed):
                    await self.close(W.NORMAL if event.code == 1005 else event.code, "")
                    self.closed = True
        return self.pending.pop(0)

    async def _raw(self, data):
        async with self.lock:
            self.writer.write(data)
            await asyncio.wait_for(self.writer.drain(), self.timeout)

    async def send(self, text):
        if self.closed:
            raise ConnectionError("socket closed")
        await self._raw(W.frame(W.TEXT, text.encode()))

    async def close(self, code=W.NORMAL, reason=""):
        if getattr(self, "_sent_close", False):
            return
        self._sent_close = True
        self.closed = True
        try:
            await self._raw(W.close_frame(code, reason))
        except (OSError, asyncio.TimeoutError):
            pass


async def _linger_close(reader, writer, *, seconds=1.0, limit=256 * 1024):
    """Close politely: stop sending, read (and drop) what the client still sends for a moment, then
    close. Closing with unread request bytes makes the kernel send a reset, which can destroy the
    response (a 503, 413 or 400) before the client reads it."""
    try:
        if writer.can_write_eof() and not writer.is_closing():
            writer.write_eof()
        end = time.monotonic() + seconds
        got = 0
        while got < limit and not reader.at_eof():
            left = end - time.monotonic()
            if left <= 0:
                break
            chunk = await asyncio.wait_for(reader.read(65536), left)
            if not chunk:
                break
            got += len(chunk)
    except (OSError, asyncio.TimeoutError, RuntimeError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def _wait_closed(reader):
    """Returns when the client closes its side (for streams, which never read again)."""
    try:
        while await reader.read(1024):
            pass
    except (ConnectionError, OSError):
        pass


def _simple(status, message, *, close=False, extra=()):
    body = (message + "\n").encode()
    hdrs = [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body))), *extra]
    if close:
        hdrs.append(("Connection", "close"))
    return H.response_head(status, hdrs) + body


# ------------------------------------------------------------------- running

REUSEPORT = hasattr(socket, "SO_REUSEPORT") and os.name == "posix" and os.uname().sysname == "Linux"


def bind(host, port, *, backlog=2048, reuse_port=False, listen=True):
    """A listening socket. With ``reuse_port`` several sockets share the port and the kernel
    spreads new connections evenly across them (one per worker process)."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if reuse_port:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    if family == socket.AF_INET6:
        try:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
    sock.bind((host, port))
    if listen:
        sock.listen(backlog)
    sock.setblocking(False)
    return sock


def run(make_site, *, host="0.0.0.0", port=8000, workers=1, logger=None, sock=None, ready=None):
    """Serve until SIGTERM/SIGINT. ``make_site()`` builds the :class:`pyweb.hosting.Site` (per worker).

    With ``workers > 1`` the parent forks that many processes, replaces any
    that die, and passes SIGTERM on for a graceful drain. On Linux each worker
    listens on its own ``SO_REUSEPORT`` socket, so the kernel balances
    connections between them; elsewhere they share one socket.
    """
    if workers <= 1 or not hasattr(os, "fork"):
        return _worker(make_site, sock or bind(host, port), logger, ready)
    split = sock is None and REUSEPORT
    # With SO_REUSEPORT the parent only holds the port (bound, not listening, so it gets no connections).
    sock = sock or bind(host, port, reuse_port=split, listen=not split)
    children = {}

    def spawn():
        pid = os.fork()
        if pid == 0:                                       # pragma: no cover - runs in the child
            try:
                mine = bind(host, sock.getsockname()[1], reuse_port=True) if split else sock
                _worker(make_site, mine, logger, None)
            finally:
                os._exit(0)
        children[pid] = time.monotonic()

    for _ in range(workers):
        spawn()
    if ready is not None:
        ready(sock.getsockname()[1])
    stopping = []

    def stop(signum, _frame):
        stopping.append(signum)
        for pid in list(children):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while children:
        try:
            pid, _status = os.wait()
        except ChildProcessError:
            break
        except InterruptedError:
            continue
        started = children.pop(pid, None)
        if not stopping and started is not None:
            if time.monotonic() - started < 1.0:
                time.sleep(1.0)                            # don't spin if a worker crashes at startup
            if logger is not None:
                logger.info(f"worker {pid} exited; starting another")
            spawn()
    return 0


def _worker(make_site, sock, logger, ready):
    site = make_site()
    if getattr(site, "start_jobs", True):
        from pyweb import jobs
        try:
            jobs.start_embedded()          # PYWEB_WORKER=0 when `pyweb worker` processes run the jobs
        except Exception as exc:  # noqa: BLE001 - serving pages matters more than running jobs here
            if logger is not None:
                logger.info(f"background jobs not started: {exc}")

    async def main():
        server = Server(site, logger=logger)
        await server.start(sock=sock)
        if ready is not None:
            ready(server.port)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError, ValueError):
                pass                                       # not the main thread (tests) or Windows
        await stop.wait()
        if logger is not None:
            logger.info("draining connections")
        await server.drain()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    return 0


class Running:
    """A server on a background thread (tests, ``pyweb.testing.serve``)."""

    def __init__(self, site, host="127.0.0.1", port=0):
        import threading
        self.site = site
        self.loop = asyncio.new_event_loop()
        self.server = Server(site)
        started = threading.Event()

        def main():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.server.start(host, port))
            started.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=main, name="pyweb-server", daemon=True)
        self.thread.start()
        if not started.wait(10):
            raise RuntimeError("server didn't start")
        self.port = self.server.port
        self.url = f"http://{host}:{self.port}"

    def stop(self, timeout=5.0):
        async def finish():
            await self.server.drain(timeout)
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        fut = asyncio.run_coroutine_threadsafe(finish(), self.loop)
        try:
            fut.result(timeout + 10)
        except Exception:  # noqa: BLE001
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        if not self.thread.is_alive():
            self.loop.close()

