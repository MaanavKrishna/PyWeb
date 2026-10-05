"""ASGI adapter: run a PyWeb app under uvicorn, hypercorn or gunicorn.

::

    # app_asgi.py
    from pyweb.asgi import create_app
    app = create_app("app.pyweb")        # or a built "dist" directory

    $ uvicorn app_asgi:app --workers 4

Page rendering and RPC calls are synchronous Python, so each request
runs in a worker thread (``asyncio.to_thread``) and never blocks the
event loop. Request bodies are capped at ``max_body`` bytes (413).
``/__pyweb/ws`` (the pages' live-update socket) works where the ASGI server
supports WebSockets (uvicorn needs ``websockets`` or ``wsproto``); elsewhere
pages fall back to Server-Sent Events by themselves.

PyWeb's own server (``pyweb serve``) needs none of this; use the adapter
when you want an ASGI server's features (HTTP/2 via hypercorn, ...).
"""

from __future__ import annotations

import asyncio

from .hosting import Site


async def _stream(stream, receive, send):
    """Send an event stream until it ends or the client disconnects."""
    async def watch():
        while (await receive())["type"] != "http.disconnect":
            pass
        stream.close()

    watcher = asyncio.ensure_future(watch())
    try:
        async for chunk in stream.aiter():
            if watcher.done():
                break
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b""})
    except OSError:
        pass
    finally:
        stream.close()
        watcher.cancel()


async def _websocket(site, scope, receive, send):
    """``/__pyweb/ws``: the page's live-update socket (see :mod:`pyweb.net.live`)."""
    headers = {}
    for k, v in scope.get("headers", []):
        name, value = k.decode("latin-1").title(), v.decode("latin-1")
        headers[name] = headers[name] + ("; " if name == "Cookie" else ", ") + value if name in headers else value
    path = scope.get("path", "/")
    if scope.get("query_string"):
        path += "?" + scope["query_string"].decode("latin-1")
    if (await receive())["type"] != "websocket.connect":
        return
    live = await asyncio.to_thread(site.websocket, path, headers, (scope.get("client") or [None])[0])
    if isinstance(live, tuple):
        await send({"type": "websocket.close", "code": 1008, "reason": live[1][:120]})
        return
    await send({"type": "websocket.accept"})
    closed = []

    async def recv():
        while True:
            msg = await receive()
            if msg["type"] == "websocket.disconnect":
                closed.append(True)
                return None
            if msg["type"] == "websocket.receive":
                return msg.get("text") if msg.get("text") is not None else msg.get("bytes")

    async def send_text(text):
        if closed:
            raise ConnectionError("socket closed")
        await send({"type": "websocket.send", "text": text})

    async def close(code, reason):
        if not closed:
            closed.append(True)
            await send({"type": "websocket.close", "code": code, "reason": reason})

    await live.run(recv, send_text, close)
    await close(1000, "")


def create_app(target="app.pyweb", *, debug=False, max_body=1_048_576, **server_kwargs):
    """Return an ASGI 3 application serving ``target`` (a `.pyweb` file or dist dir)."""
    site = Site(target, debug=debug, max_body=max_body, **server_kwargs)

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    from .hosting import shutdown
                    await asyncio.to_thread(shutdown)
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] == "websocket":
            await _websocket(site, scope, receive, send)
            return
        if scope["type"] != "http":
            return
        chunks, size, too_big = [], 0, False
        from .hosting import body_limit
        limit = body_limit(scope.get("path", ""), max_body)
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            part = msg.get("body", b"")
            size += len(part)
            if size > limit:
                too_big = True
            elif part:
                chunks.append(part)
            if not msg.get("more_body"):
                break
        headers = {}
        for k, v in scope.get("headers", []):
            name, value = k.decode("latin-1").title(), v.decode("latin-1")
            if name in headers:  # repeated (HTTP/2 sends cookies as several headers)
                value = headers[name] + ("; " if name == "Cookie" else ", ") + value
            headers[name] = value
        path = scope.get("path", "/")
        if scope.get("query_string"):
            path += "?" + scope["query_string"].decode("latin-1")
        if too_big:
            status, hdrs, body = 413, [("Content-Type", "application/json")], \
                b'{"error": {"code": "http_413", "message": "request body too large"}}'
        else:
            status, hdrs, body = await asyncio.to_thread(
                site.respond, scope.get("method", "GET"), path, headers, b"".join(chunks),
                (scope.get("client") or [None])[0])
        raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in hdrs]
        if hasattr(body, "aiter"):  # Server-Sent Events, streamed RPC results
            await send({"type": "http.response.start", "status": status, "headers": raw_headers})
            await _stream(body, receive, send)
            return
        if not any(k == b"content-length" for k, _ in raw_headers):
            raw_headers.append((b"content-length", str(len(body)).encode()))
        await send({"type": "http.response.start", "status": status, "headers": raw_headers})
        await send({"type": "http.response.body", "body": body})

    app.site = site
    return app
