"""PyWeb — Python from browser to database."""

from .app import App
from .models import Email, Model
from . import auth, cache, jobs, realtime, security, observability, forms, testing
from . import sync, deploy, uploads, lsp, live
from . import browser as browser_api
from . import build, css, plugins, platform
from .jobs import task
from .context import NotFound, head, redirect, request, session
from .rpc import RPCError
from .decorators import component, edge, server, worker
from .realtime import channel, publish, subscribe
from .packages import npm

__all__ = [
    "App",
    "request",
    "session",
    "redirect",
    "head",
    "NotFound",
    "RPCError",
    "component",
    "edge",
    "server",
    "worker",
    "channel",
    "publish",
    "subscribe",
    "npm",
    "Email",
    "Model",
    "task",
    "auth",
    "cache",
    "jobs",
    "realtime",
    "security",
    "observability",
    "forms",
    "testing",
    "sync",
    "live",
    "deploy",
    "uploads",
    "lsp",
    "browser_api",
    "build",
    "css",
    "plugins",
    "platform",
]

def _version():
    """Single source of truth: installed dist metadata, else pyproject."""
    try:
        from importlib.metadata import version, PackageNotFoundError
        try:
            return version("pyweb-stack")
        except PackageNotFoundError:
            pass
    except ImportError:  # pragma: no cover - ancient Python
        pass
    import os
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(root, "pyproject.toml")) as fh:
            m = re.search(r'^version\s*=\s*"([^"]+)"', fh.read(), re.M)
            if m:
                return m.group(1)
    except OSError:
        pass
    return "0.0.0+unknown"


__version__ = _version()
