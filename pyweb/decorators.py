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
        _field_defaults(target)
        _mark(target, location="server")
        if guard is not None:
            target.__pyweb_guard__ = guard
        return target
    return apply(fn) if fn is not None else apply



def _field_defaults(fn):
    """``title: str = Field(max=120)`` means ``title: Annotated[str, Field(max=120)]`` (with the
    Field's default, or required): rewrite the signature so every layer sees the rules."""
    import inspect
    import typing
    try:
        from pyweb.models.fields import MISSING, Field
        sig = inspect.signature(fn)
    except (TypeError, ValueError, ImportError):
        return
    params, changed = [], False
    annotations = dict(getattr(fn, "__annotations__", {}) or {})
    for p in sig.parameters.values():
        if isinstance(p.default, Field):
            field = p.default
            ann = p.annotation if p.annotation is not p.empty else str
            if isinstance(ann, str):          # `from __future__ import annotations`: resolve it now
                try:
                    ann = eval(ann, getattr(fn, "__globals__", {}))  # noqa: S307 - the function's own annotation
                except Exception:  # noqa: BLE001
                    ann = str
            ann = typing.Annotated[ann, field]
            default = p.empty if field.default is MISSING else field.default
            p = p.replace(annotation=ann, default=default)
            annotations[p.name] = ann
            changed = True
        params.append(p)
    if not changed:
        return
    try:
        new_sig = sig.replace(parameters=params)
    except ValueError:                        # a required Field after a defaulted parameter: keep it optional
        params = [p.replace(default=None) if p.default is p.empty and isinstance(sig.parameters[p.name].default, Field)
                  else p for p in params]
        new_sig = sig.replace(parameters=params)
    fn.__signature__ = new_sig
    fn.__annotations__ = annotations
    defaults = tuple(p.default for p in params if p.default is not p.empty
                     and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD))
    kwdefaults = {p.name: p.default for p in params if p.kind is p.KEYWORD_ONLY and p.default is not p.empty}
    if hasattr(fn, "__defaults__"):
        try:
            fn.__defaults__ = defaults or None
            fn.__kwdefaults__ = kwdefaults or None
        except (AttributeError, TypeError):
            pass


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
