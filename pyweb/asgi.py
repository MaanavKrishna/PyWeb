"""ASGI adapter: run a PyWeb app under uvicorn, hypercorn or gunicorn.

::

    # app_asgi.py
    from pyweb.asgi import create_app
    app = create_app("app.pyweb")        # or a built "dist" directory

    $ uvicorn app_asgi:app --workers 4

Page rendering and RPC calls are synchronous Python, so each request
runs in a worker thread (``asyncio.to_thread``) and never blocks the
event loop. Request bodies are capped at ``max_body`` bytes (413).
"""

from __future__ import annotations

import asyncio

from .hosting import Site


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
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        chunks, size, too_big = [], 0, False
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            part = msg.get("body", b"")
            size += len(part)
            if size > max_body:
                too_big = True
            elif part:
                chunks.append(part)
            if not msg.get("more_body"):
                break
        headers = {}
        for k, v in scope.get("headers", []):
            name = k.decode("latin-1").title()
            headers[name] = v.decode("latin-1")
        path = scope.get("path", "/")
        if scope.get("query_string"):
            path += "?" + scope["query_string"].decode("latin-1")
        if too_big:
            status, hdrs, body = 413, [("Content-Type", "application/json")], \
                b'{"error": {"code": "http_413", "message": "request body too large"}}'
        else:
            status, hdrs, body = await asyncio.to_thread(
                site.respond, scope.get("method", "GET"), path, headers, b"".join(chunks))
        raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in hdrs]
        if not any(k == b"content-length" for k, _ in raw_headers):
            raw_headers.append((b"content-length", str(len(body)).encode()))
        await send({"type": "http.response.start", "status": status, "headers": raw_headers})
        await send({"type": "http.response.body", "body": body})

    app.site = site
    return app
