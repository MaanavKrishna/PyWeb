"""Check and convert RPC arguments against a server function's type hints.

Browsers send JSON, so every argument is checked before the function runs:
a ``name: str`` never receives a dict, ``ids: list[int]`` gets a list of
ints, a ``Model`` or dataclass parameter is built from an object with only
its own fields. Anything else is a ``validation_error`` naming the field.

Scalars are converted the way forms need (``"5"`` is fine for ``int``);
containers and objects are checked strictly.
"""

from __future__ import annotations

import dataclasses
import inspect
import types
import typing

_HINTS: dict = {}


class ArgError(ValueError):
    pass


def hints(fn):
    """``{param: annotation}``, resolving string annotations when possible (cached)."""
    found = _HINTS.get(fn)
    if found is None:
        try:
            found = typing.get_type_hints(fn)
        except Exception:  # noqa: BLE001 - unresolvable names: fall back to the raw annotations
            found = {k: p.annotation for k, p in inspect.signature(fn).parameters.items()
                     if p.annotation is not inspect.Parameter.empty}
        _HINTS[fn] = found
    return found


def _name(ann):
    return getattr(ann, "__name__", None) or str(ann).replace("typing.", "")


def check(value, ann, where):
    """``value`` as type ``ann`` or raise :class:`ArgError` mentioning ``where``."""
    if ann is inspect.Parameter.empty or ann is typing.Any or ann is object:
        return value
    if isinstance(ann, str):                       # an annotation we couldn't resolve
        return _by_name(value, ann, where)
    origin = typing.get_origin(ann)
    args = typing.get_args(ann)
    if origin is typing.Union or (types.UnionType is not None and isinstance(ann, types.UnionType)):
        if value is None and type(None) in args:
            return None
        errors = []
        for option in (a for a in args if a is not type(None)):
            try:
                return check(value, option, where)
            except ArgError as exc:
                errors.append(str(exc))
        raise ArgError(f"{where} expects {_name(ann)}")
    if origin is typing.Literal:
        if value in args:
            return value
        raise ArgError(f"{where} must be one of {', '.join(repr(a) for a in args)}")
    if origin in (list, set, frozenset, tuple) or ann in (list, set, frozenset, tuple):
        if not isinstance(value, list):
            raise ArgError(f"{where} expects a list")
        if origin is tuple and args and args[-1] is not Ellipsis:
            if len(value) != len(args):
                raise ArgError(f"{where} expects {len(args)} items")
            return tuple(check(v, a, f"{where}[{i}]") for i, (v, a) in enumerate(zip(value, args)))
        item = args[0] if args else typing.Any
        items = [check(v, item, f"{where}[{i}]") for i, v in enumerate(value)]
        kind = origin or ann
        return items if kind is list else kind(items)
    if origin is dict or ann is dict:
        if not isinstance(value, dict):
            raise ArgError(f"{where} expects an object")
        vt = args[1] if len(args) == 2 else typing.Any
        return {str(k): check(v, vt, f"{where}.{k}") for k, v in value.items()}
    if ann is type(None):
        if value is None:
            return None
        raise ArgError(f"{where} must be null")
    if isinstance(ann, type):
        return _by_type(value, ann, where)
    return value


def _scalar(value, ann, where):
    if isinstance(value, (dict, list)) or value is None:
        raise ArgError(f"{where} expects {_name(ann)}")
    try:
        if ann is bool:
            if isinstance(value, str):
                low = value.strip().lower()
                if low in ("1", "true", "yes", "on"):
                    return True
                if low in ("0", "false", "no", "off", ""):
                    return False
                raise ValueError(value)
            return bool(value)
        if ann is int:
            if isinstance(value, float) and not value.is_integer():
                raise ValueError(value)
            return int(value)
        if ann is float:
            return float(value)
        return str(value) if not isinstance(value, bool) else str(value).lower()
    except (TypeError, ValueError):
        raise ArgError(f"{where} expects {_name(ann)}") from None


def _by_type(value, ann, where):
    from pyweb.models import Email, Model
    if ann in (int, float, bool, str):
        return _scalar(value, ann, where)
    if issubclass(ann, Email):
        text = _scalar(value, str, where)
        if "@" not in text:
            raise ArgError(f"{where} must be a valid email")
        return ann(text)
    if dataclasses.is_dataclass(ann):
        if not isinstance(value, dict):
            raise ArgError(f"{where} expects an object")
        fields = {f.name: f for f in dataclasses.fields(ann) if f.init}
        extra = set(value) - set(fields)
        if extra:
            raise ArgError(f"{where} has unknown field(s): {', '.join(sorted(extra))}")
        field_hints = hints(ann)
        kwargs = {k: check(v, field_hints.get(k, typing.Any), f"{where}.{k}") for k, v in value.items()}
        missing = [n for n, f in fields.items() if n not in kwargs
                   and f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING]
        if missing:
            raise ArgError(f"{where} is missing {', '.join(missing)}")
        return ann(**kwargs)
    if issubclass(ann, Model):
        if not isinstance(value, dict):
            raise ArgError(f"{where} expects an object")
        known = getattr(ann, "__fields__", {}) or {}
        extra = set(value) - set(known) if known else set()
        if extra:
            raise ArgError(f"{where} has unknown field(s): {', '.join(sorted(extra))}")
        field_hints = {k: v for k, v in hints(ann).items() if k in known} if known else {}
        try:
            return ann(**{k: check(v, field_hints.get(k, typing.Any), f"{where}.{k}") for k, v in value.items()})
        except TypeError as exc:
            raise ArgError(f"{where}: {exc}") from None
    if isinstance(value, ann):
        return value
    return value          # an annotation we don't know how to build: pass the JSON value through


def _by_name(value, ann, where):
    """Checks for annotations left as strings (names we couldn't resolve)."""
    base = ann.split("[", 1)[0].strip()
    simple = {"int": int, "Integer": int, "float": float, "Float": float, "Decimal": float,
              "bool": bool, "Boolean": bool, "str": str}
    if base in simple:
        return _scalar(value, simple[base], where)
    if base in ("list", "List") and not isinstance(value, list):
        raise ArgError(f"{where} expects a list")
    if base in ("dict", "Dict") and not isinstance(value, dict):
        raise ArgError(f"{where} expects an object")
    if "Email" in ann and "@" not in str(value):
        raise ArgError(f"{where} must be a valid email")
    return value


def check_args(fn, args: dict, signature):
    """Arguments for ``fn`` from the JSON ``args`` object, checked and converted."""
    params = signature.parameters
    takes_kwargs = any(p.kind is p.VAR_KEYWORD for p in params.values())
    unknown = [k for k in args if k not in params]
    if unknown and not takes_kwargs:
        raise ArgError(f"{fn.__name__} got unknown argument(s): {', '.join(sorted(unknown))}")
    found = hints(fn)
    out = {}
    for pname, param in params.items():
        if param.kind in (param.VAR_KEYWORD, param.VAR_POSITIONAL):
            continue
        if pname in args:
            value = args[pname]
            if value is None and param.default is None:
                out[pname] = None
                continue
            out[pname] = check(value, found.get(pname, param.annotation), f"{fn.__name__}.{pname}")
        elif param.default is inspect.Parameter.empty:
            raise ArgError(f"{fn.__name__} missing required argument {pname!r}")
    if takes_kwargs:
        out.update({k: args[k] for k in unknown})
    return out
