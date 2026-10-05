"""PyWeb — Python from browser to database."""

from .app import App
from .models import Email, Field, Model
from .rules import ValidationError
from .db import atomic
from .auth import current_user
from . import auth, cache, jobs, realtime, security, observability, forms, testing
from . import sync, deploy, uploads, lsp, livetable
from . import browser as browser_api
from . import build, css, plugins, platform
from .jobs import task
from .context import NotFound, head, redirect, request, session
from .rpc import RPCError
from .decorators import component, edge, server, worker
from .rooms import join, presence
from .realtime import channel, publish, subscribe
from .packages import npm
from .markdown import Markdown
from .forms import Checkbox, FileInput, Form, FormError, Input, Select, Submit, Textarea, UploadedFile
from .livedata import live

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
    "join",
    "presence",
    "publish",
    "subscribe",
    "npm",
    "Markdown",
    "Form",
    "Input",
    "Textarea",
    "Select",
    "Checkbox",
    "FileInput",
    "Submit",
    "FormError",
    "UploadedFile",
    "live",
    "Email",
    "Field",
    "Model",
    "ValidationError",
    "atomic",
    "current_user",
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
    "livetable",
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
