"""PyWeb — Python from browser to database."""

# Every public name loads on first use (PEP 562), so `import pyweb` and the command line start
# fast: `from pyweb import App, server` loads what it names, and `pyweb --version` loads nothing
# else. (Python 3.15's `lazy import` would do this too; this works on every supported version.)
_LAZY = {
    "App": (".app", "App"),
    "Email": (".models", "Email"), "Field": (".models", "Field"), "Model": (".models", "Model"),
    "ValidationError": (".rules", "ValidationError"),
    "atomic": (".db", "atomic"),
    "current_user": (".auth", "current_user"),
    "task": (".jobs", "task"),
    "NotFound": (".context", "NotFound"), "head": (".context", "head"), "redirect": (".context", "redirect"),
    "request": (".context", "request"), "session": (".context", "session"),
    "RPCError": (".rpc", "RPCError"),
    "component": (".decorators", "component"), "edge": (".decorators", "edge"),
    "server": (".decorators", "server"), "worker": (".decorators", "worker"),
    "join": (".rooms", "join"), "presence": (".rooms", "presence"),
    "channel": (".realtime", "channel"), "publish": (".realtime", "publish"), "subscribe": (".realtime", "subscribe"),
    "npm": (".packages", "npm"),
    "Markdown": (".markdown", "Markdown"),
    "ErrorBoundary": (".errorboundary", "ErrorBoundary"),
    "_": (".i18n", "gettext"), "gettext": (".i18n", "gettext"), "ngettext": (".i18n", "ngettext"),
    "Checkbox": (".forms", "Checkbox"), "FileInput": (".forms", "FileInput"), "Form": (".forms", "Form"),
    "FormError": (".forms", "FormError"), "Input": (".forms", "Input"), "Select": (".forms", "Select"),
    "Submit": (".forms", "Submit"), "Textarea": (".forms", "Textarea"), "UploadedFile": (".forms", "UploadedFile"),
    "live": (".livedata", "live"),
    "browser_api": (".browser", None),
}
_SUBMODULES = {"i18n", "auth", "cache", "jobs", "realtime", "security", "observability", "forms", "testing", "sync",
               "deploy", "uploads", "lsp", "livetable", "build", "css", "plugins", "platform"}


def __getattr__(name):
    import importlib
    if name in _LAZY:
        module, attr = _LAZY[name]
        mod = importlib.import_module(module, __name__)
        value = mod if attr is None else getattr(mod, attr)
    elif name in _SUBMODULES:
        value = importlib.import_module("." + name, __name__)
    elif name == "__version__":
        value = _version()
    else:
        raise AttributeError(f"module 'pyweb' has no attribute {name!r}")
    globals()[name] = value            # once loaded, a plain attribute
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__) | {"__version__"})


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
    "ErrorBoundary",
    "gettext",
    "ngettext",
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
    "browser_api",
    "platform",
]
# Also importable from `pyweb` but not part of `from pyweb import *` (tooling, or still experimental):
# sync, livetable, deploy, uploads, lsp, build, css, plugins.

def _version():
    """Single source of truth: installed dist metadata, else pyproject."""
    import os
    import sys
    # Read the installed METADATA directly: importlib.metadata (and the email package it
    # imports) costs ~20 ms, a third of `pyweb --version`.
    for entry in sys.path:
        try:
            names = os.listdir(entry or ".")
        except OSError:
            continue
        for name in names:
            if name.startswith("pyweb_stack-") and name.endswith(".dist-info"):
                try:
                    with open(os.path.join(entry or ".", name, "METADATA"), encoding="utf-8") as fh:
                        for line in fh:
                            if line.startswith("Version:"):
                                return line.split(":", 1)[1].strip()
                            if not line.strip():
                                break
                except OSError:
                    pass
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

