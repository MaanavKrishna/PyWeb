"""Where the app is running: in a browser tab, as an installed app, or as a desktop app.

```python
from pyweb import platform

@app.page("/")
def Home():
    if platform.desktop:
        <button onclick={export}>Save to a file…</button>
    else:
        <a href="/export.txt" download>Download</a>
```

* ``platform.desktop``: inside ``pyweb desktop`` or a packaged desktop app.
* ``platform.pwa``: this request comes from the installed web app (``App(pwa=...)``,
  opened from the home screen or dock). The browser says so in a cookie, so use it for
  layout, never for security.
* ``platform.installed``: ``desktop or pwa``.
* ``platform.web``: not the desktop app (a browser tab or the installed web app).
* ``platform.server``: always True (this code runs on the server).

``mobile`` and ``edge`` are always False: they were placeholders in 0.x and there are no
such targets (``pyweb upgrade`` points at them).

The values are read when used, so a page can branch per request.
"""

from __future__ import annotations

import os

server = True
mobile = False
edge = False

#: Branch names :func:`prune` knows.
TARGETS = ("web", "ios", "android", "desktop", "edge", "server")


def _desktop() -> bool:
    if os.environ.get("PYWEB_DESKTOP", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return os.environ.get("PYWEB_TARGET", "") == "desktop"


def _pwa() -> bool:
    from pyweb import context
    try:
        req = context.current().request
    except (RuntimeError, LookupError, AttributeError):
        return False
    cookies = getattr(req, "cookies", None) or {}
    return cookies.get("pyweb_display") == "standalone"


def target() -> str:
    """``"desktop"`` in the desktop app, else ``"web"``."""
    return "desktop" if _desktop() else "web"


def __getattr__(name):
    if name == "desktop":
        return _desktop()
    if name == "web":
        return not _desktop()
    if name == "pwa":
        return _pwa()
    if name == "installed":
        return _desktop() or _pwa()
    raise AttributeError(f"module 'pyweb.platform' has no attribute {name!r}")


def prune(source: str, keep: str) -> str:
    """Drop ``if platform.<other>:`` branches from ``source`` for the ``keep`` target.

    Keeps ``if platform.<keep>:`` blocks, drops the other platform branches, unwraps shared
    code. Operates on source text with ``ast``, so pruning is exact, not regex-based.
    """
    import ast as _ast
    tree = _ast.parse(source)
    keep_attr = {"ios": "mobile", "android": "mobile"}.get(keep, keep)

    class Pruner(_ast.NodeTransformer):
        def visit_If(self, node):  # noqa: N802
            self.generic_visit(node)
            test = node.test
            if (isinstance(test, _ast.Attribute)
                    and isinstance(test.value, _ast.Name)
                    and test.value.id == "platform"):
                if test.attr == keep_attr or (
                        keep_attr == "mobile" and test.attr == "mobile"):
                    return node.body
                if test.attr in ("web", "mobile", "desktop", "edge",
                                 "server"):
                    return node.orelse or None
            return node

    pruned = Pruner().visit(tree)
    _ast.fix_missing_locations(pruned)
    return _ast.unparse(pruned)
