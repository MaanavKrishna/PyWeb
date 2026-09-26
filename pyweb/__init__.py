"""PyWeb — Python from browser to database."""

from .app import App
from .reactive import Computed, Effect, Resource, Signal, live
from .models import Email, Model
from . import auth, cache, jobs, realtime, security, observability, forms, testing
from . import sync, live, deploy, uploads, lsp
from . import browser as browser_api
from . import build, css
from .jobs import task
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
    "live",
    "deploy",
    "uploads",
    "lsp",
    "browser_api",
    "build",
    "css",
]

__version__ = "0.1.0"
