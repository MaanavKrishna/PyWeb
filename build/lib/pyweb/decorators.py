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


def server(fn=None, *, login=False, roles=(), fresh=None):
    """Make a function callable from the browser. ``login=True`` requires a signed-in
    user (401), ``roles=[...]`` those roles too (403), ``fresh=600`` a sign-in in
    the last 600 seconds."""
    guard = {"login": True, "roles": list(roles), "fresh": fresh} if (login or roles or fresh) else None

    def apply(target):
        _mark(target, location="server")
        if guard is not None:
            target.__pyweb_guard__ = guard
        return target
    return apply(fn) if fn is not None else apply



def edge(fn=None):
    return _mark(fn, location="edge") if fn else lambda t: _mark(t, location="edge")


def worker(fn=None):
    return _mark(fn, location="worker") if fn else lambda t: _mark(t, location="worker")



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
