"""Placement and component markers. All are metadata-only at runtime."""

from __future__ import annotations


def _mark(fn_or_cls, **flags):
    if fn_or_cls is None:
        def deco(target):
            return _mark(target, **flags)
        return deco
    for key, value in flags.items():
        setattr(fn_or_cls, f"__pyweb_{key}__", value)
    return fn_or_cls


def component(fn=None):
    return _mark(fn, component=True) if fn else lambda t: _mark(t, component=True)


def server(fn=None):
    return _mark(fn, location="server") if fn else lambda t: _mark(t, location="server")


def browser(fn=None):
    return _mark(fn, location="browser") if fn else lambda t: _mark(t, location="browser")


def edge(fn=None):
    return _mark(fn, location="edge") if fn else lambda t: _mark(t, location="edge")


def worker(fn=None):
    return _mark(fn, location="worker") if fn else lambda t: _mark(t, location="worker")


def shared(fn=None):
    return _mark(fn, location="shared") if fn else lambda t: _mark(t, location="shared")


def csrf_exempt(fn=None):
    """Skip the CSRF check for this RPC (safe methods / token endpoints)."""
    return _mark(fn, csrf_exempt=True) if fn else lambda t: _mark(t, csrf_exempt=True)


def auth_required(fn=None):
    """Require an authenticated session to call this RPC."""
    return _mark(fn, auth="required") if fn else lambda t: _mark(t, auth="required")


def rate_limited(fn=None, *, max_calls=60, window=60.0):
    """Per-RPC rate-limit hint; enforced when a Server has a limiter."""
    def apply(target):
        _mark(target, rate_limit=(max_calls, window))
        return target
    return apply(fn) if fn else apply
