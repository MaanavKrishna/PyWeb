"""Top-level PyWeb app: routing, @page render strategies, SSR streaming, RPC."""

from __future__ import annotations

import html as _html
import inspect

from pyweb.runtime.server import (
    HEALTH_PATH,
    Request,
    Response,
    Server,
    new_csrf_token,
)

RENDER_STRATEGIES = ("static", "server", "stream")


def escape(text: object) -> str:
    return _html.escape(str(text), quote=True)


class Page:
    def __init__(self, path: str, fn, render: str = "server", title: str = ""):
        if render not in RENDER_STRATEGIES:
            raise ValueError(f"unknown render strategy: {render!r}")
        self.path = path
        self.fn = fn
        self.render = render
        self.title = title or path


class PyWeb:
    """User-facing app object. Owns a Server, pages, RPCs, and SSR."""

    def __init__(self, secret: str = "dev-secret", static_files: dict | None = None):
        from pyweb.runtime.server import (
            CSRFMiddleware,
            GzipMiddleware,
            RateLimitMiddleware,
            SecurityHeadersMiddleware,
            SessionMiddleware,
        )

        self.secret = secret
        self.server = Server(middlewares=[
            SecurityHeadersMiddleware(),
            GzipMiddleware(),
            SessionMiddleware(secret),
            CSRFMiddleware(),
            RateLimitMiddleware(),
        ])
        if static_files:
            from pyweb.runtime.server import StaticFilesMiddleware

            self.server.middlewares.append(StaticFilesMiddleware(static_files))
        self.pages: dict[str, Page] = {}
        self._startup_hooks = self.server._startup_hooks
        self._shutdown_hooks = self.server._shutdown_hooks

    # -- API -----------------------------------------------------------
    def page(self, path: str, render: str = "server", title: str = ""):
        def deco(fn):
            page = Page(path, fn, render=render, title=title)
            self.pages[path] = page

            def handler(request: Request):
                return self._render(page, request)

            self.server.routes.append((path, {"GET"}, handler))
            return fn

        return deco

    def rpc(self, name: str | None = None):
        def deco(fn):
            self.server.rpc(name or fn.__name__)(fn)
            self._register_sig(name or fn.__name__, fn)
            return fn

        return deco

    _rpc_sigs: dict = {}

    def _register_sig(self, name, fn):
        try:
            self._rpc_sigs[name] = str(inspect.signature(fn))
        except (TypeError, ValueError):
            self._rpc_sigs[name] = "(...)"

    def route(self, path, methods=("GET",)):
        return self.server.route(path, methods=methods)

    def on_startup(self, fn):
        return self.server.on_startup(fn)

    def on_shutdown(self, fn):
        return self.server.on_shutdown(fn)

    def handle(self, request: Request) -> Response:
        return self.server.handle(request)

    def asgi(self):
        return self.server.asgi()

    def run(self, *a, **kw):
        return self.server.run(*a, **kw)

    # -- SSR -----------------------------------------------------------
    def render_page(self, path: str, request: Request | None = None) -> Response:
        page = self.pages.get(path)
        if page is None:
            return Response.text("not found", status=404)
        return self._render(page, request or Request(path=path))

    def _render(self, page: Page, request: Request) -> Response:
        request.session.setdefault("csrf", new_csrf_token())
        try:
            content = page.fn(request) if "request" in self._fn_params(page.fn) else page.fn()
        except Exception as exc:  # noqa: BLE001
            return Response.json({"error": "render failed", "detail": str(exc)}, status=500)
        if isinstance(content, Response):
            return content
        body = str(content)
        if page.render == "stream":
            chunks = list(self.stream_chunks(page, body))
            return Response(b"".join(c.encode() for c in chunks),
                            headers={"content-type": "text/html; charset=utf-8"})
        return Response.html(self.layout(page.title, body))

    @staticmethod
    def _fn_params(fn) -> set:
        try:
            return set(inspect.signature(fn).parameters)
        except (TypeError, ValueError):
            return set()

    def layout(self, title: str, body: str) -> str:
        return (
            "<!DOCTYPE html><html><head>"
            f"<meta charset='utf-8'><title>{escape(title)}</title>"
            "</head><body>" + body + "</body></html>"
        )

    def stream_chunks(self, page: Page, body: str):
        """Yield <head> first, then body chunks, then the closing tags."""
        head = (
            "<!DOCTYPE html><html><head>"
            f"<meta charset='utf-8'><title>{escape(page.title)}</title>"
            "</head><body>"
        )
        yield head
        size = 1024
        for i in range(0, len(body), size):
            yield body[i:i + size]
        yield "</body></html>"

    # -- health ---------------------------------------------------------
    def health(self) -> dict:
        return {"status": "ok", "pages": sorted(self.pages),
                "rpc": sorted(self.server.rpc_handlers),
                "health_path": HEALTH_PATH}
