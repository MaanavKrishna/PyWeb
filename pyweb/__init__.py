"""PyWeb — Python from browser to database."""

from .app import App
from .reactive import Computed, Effect, Resource, Signal, live
from .models import Email, Model
from . import auth, cache, jobs, realtime, security, observability, forms, testing
from . import sync, deploy, uploads, lsp
from . import live as live_queries  # noqa: F401 - module; `live` is the function
from . import browser as browser_api
from . import build, css, plugins, platform
from .jobs import task
from .context import NotFound, redirect, request, session
from .rpc import RPCError
# NOTE: decorators import comes last — importing the ``pyweb.browser``
# submodule rebinds the ``browser`` package attr to the module, so the
# ``@browser`` decorator must be bound afterwards. ``import pyweb.browser``
# still resolves to the module via sys.modules either way.
from .decorators import (
    browser,
    component,
    edge,
    server,
    shared,
    worker,
)

__all__ = [
    "App",
    "request",
    "session",
    "redirect",
    "NotFound",
    "RPCError",
    "browser",
    "component",
    "edge",
    "server",
    "shared",
    "worker",
    "Computed",
    "Effect",
    "Resource",
    "Signal",
    "live",
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
    "live_queries",
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
            return version("pyweb")
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
