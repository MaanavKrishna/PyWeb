"""An OpenAPI 3.1 document for an app's server functions: ``pyweb openapi app.pyweb``.

Every ``@server`` function is a ``POST /__pyweb/rpc/<name>`` operation whose
body is ``{"args": {...}}`` and whose answer is ``{"result": ...}``. Argument
schemas come from the type hints and ``Field`` rules (lengths, ranges,
patterns, choices), the summary from the docstring, and the security from
the guards (``login=``, ``roles=``, ``org_role=``...). Give the document to
Swagger UI, Postman or a client generator; call with an API token
(``Authorization: Bearer pwk_...``, see ``/account/tokens``).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import decimal
import inspect
import json
import typing
import uuid

ERROR = {"type": "object", "properties": {"error": {"type": "object", "properties": {
    "code": {"type": "string"}, "message": {"type": "string"}, "details": {}}}}}


def _field_rules(schema, field):
    rules = {}
    kind = schema.get("type")
    if field.min is not None:
        rules["minLength" if kind == "string" else "minItems" if kind == "array" else "minimum"] = field.min
    if field.max is not None:
        rules["maxLength" if kind == "string" else "maxItems" if kind == "array" else "maximum"] = field.max
    if field.pattern:
        rules["pattern"] = field.pattern
    if field.choices:
        rules["enum"] = list(field.choices)
    if field.format in ("email", "uri"):
        rules["format"] = field.format
    elif field.format == "url":
        rules["format"] = "uri"
    if field.help or field.label:
        rules["description"] = field.help or field.label
    return {**schema, **rules}


def schema_for(tp, components=None):
    """A JSON Schema for the Python type ``tp`` (as server functions accept it)."""
    components = {} if components is None else components
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if tp is inspect.Parameter.empty or tp is typing.Any or tp is None:
        return {}
    if origin is typing.Annotated:
        from pyweb.models.fields import Field
        base = schema_for(args[0], components)
        for extra in args[1:]:
            if isinstance(extra, Field):
                base = _field_rules(base, extra)
        return base
    if origin is typing.Union or (origin is not None and type(tp).__name__ == "UnionType"):
        parts = [schema_for(a, components) for a in args if a is not type(None)]
        nullable = any(a is type(None) for a in args)
        one = parts[0] if len(parts) == 1 else {"anyOf": parts}
        return {"anyOf": [one, {"type": "null"}]} if nullable else one
    if origin is typing.Literal:
        return {"enum": list(args)}
    if origin in (list, set, tuple, frozenset) or tp in (list, set, tuple):
        out = {"type": "array"}
        if args:
            out["items"] = schema_for(args[0], components)
        return out
    if origin is dict or tp is dict:
        out = {"type": "object"}
        if len(args) == 2:
            out["additionalProperties"] = schema_for(args[1], components)
        return out
    if isinstance(tp, type):
        if tp is bool:
            return {"type": "boolean"}
        if tp is int:
            return {"type": "integer"}
        if tp in (float, decimal.Decimal):
            return {"type": "number"}
        if issubclass(tp, str):
            name = tp.__name__.lower()
            return {"type": "string", **({"format": "email"} if name == "email" else
                                         {"format": "uri"} if name == "url" else {})}
        if tp is bytes:
            return {"type": "string", "contentEncoding": "base64"}
        if tp is dt.datetime:
            return {"type": "string", "format": "date-time"}
        if tp is dt.date:
            return {"type": "string", "format": "date"}
        if tp is uuid.UUID:
            return {"type": "string", "format": "uuid"}
        model_meta = getattr(tp, "_pyweb_meta", None)
        if model_meta is not None:
            return {"$ref": "#/components/schemas/" + _model_schema(tp, components)}
        if dataclasses.is_dataclass(tp):
            return {"$ref": "#/components/schemas/" + _dataclass_schema(tp, components)}
    return {}


def _model_schema(model, components):
    name = model.__name__
    if name not in components:
        components[name] = {}
        props, required = {}, []
        for f in model._meta.fields.values():
            if f.private or f.encrypted:
                continue
            base = schema_for(f.py_type, components) if f.py_type is not None else {}
            prop = _field_rules(base, f)
            if f.readonly:
                prop["readOnly"] = True
            props[f.name] = prop
            if f.required and not f.readonly:
                required.append(f.name)
        components[name] = {"type": "object", "properties": props, **({"required": required} if required else {})}
    return name


def _dataclass_schema(cls, components):
    name = cls.__name__
    if name not in components:
        components[name] = {}
        hints = typing.get_type_hints(cls, include_extras=True)
        props, required = {}, []
        for f in dataclasses.fields(cls):
            props[f.name] = schema_for(hints.get(f.name), components)
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
                required.append(f.name)
        components[name] = {"type": "object", "properties": props, **({"required": required} if required else {})}
    return name


def _operation(name, fn, components):
    sig = inspect.signature(fn)
    try:
        hints = typing.get_type_hints(fn, include_extras=True)
    except Exception:  # noqa: BLE001 - an unresolvable hint: describe what we can
        hints = {}
    props, required = {}, []
    for p in sig.parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        schema = schema_for(hints.get(p.name, p.annotation), components)
        if p.default is not p.empty:
            try:
                json.dumps(p.default)
                schema = {**schema, "default": p.default}
            except (TypeError, ValueError):
                pass
        else:
            required.append(p.name)
        props[p.name] = schema
    args = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        args["required"] = required
    doc = inspect.getdoc(fn) or ""
    op = {
        "operationId": name,
        "summary": doc.splitlines()[0] if doc else name.replace("_", " "),
        "requestBody": {"required": True, "content": {"application/json": {"schema": {
            "type": "object", "properties": {"args": args}, "required": ["args"]}}}},
        "responses": {
            "200": {"description": "The result", "content": {"application/json": {"schema": {
                "type": "object", "properties": {"result": schema_for(hints.get("return"), components)}}}}},
            "401": {"description": "Not signed in (or the token isn't valid)",
                    "content": {"application/json": {"schema": ERROR}}},
            "403": {"description": "Not allowed", "content": {"application/json": {"schema": ERROR}}},
            "422": {"description": "Arguments that break the rules",
                    "content": {"application/json": {"schema": ERROR}}},
            "429": {"description": "Too many calls", "content": {"application/json": {"schema": ERROR}}},
        },
    }
    if len(doc.splitlines()) > 1:
        op["description"] = doc
    guard = getattr(fn, "__pyweb_guard__", None)
    if guard:
        op["security"] = [{"apiToken": []}, {"session": []}]
        notes = []
        if guard.get("roles"):
            notes.append("roles: " + ", ".join(guard["roles"]))
        if guard.get("org_roles"):
            notes.append("organization role: " + " or ".join(guard["org_roles"]))
        if guard.get("feature"):
            notes.append("plan feature: " + guard["feature"])
        if guard.get("fresh"):
            notes.append("needs a recent interactive sign-in (API tokens can't call it)")
        if notes:
            op["x-pyweb-requires"] = notes
    else:
        op["security"] = [{}, {"apiToken": []}, {"session": []}]
    rate = getattr(fn, "__pyweb_rate__", None)
    if rate:
        op["x-pyweb-rate-limit"] = {"calls": rate[0], "seconds": rate[1], "per": rate[2]}
    if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn):
        op["responses"]["200"] = {"description": "Streamed results, one JSON object per line ({\"chunk\": ...})",
                                  "content": {"application/x-ndjson": {"schema": {}}}}
    return op


def _openapi_path(path):
    import re
    return re.sub(r"\{(\w+)(?::\w+)?\}", r"{\1}", path)


def _route_operation(route, method, components):
    """An ``@app.route`` endpoint: path parameters, query parameters, a JSON body for writes."""
    sig = inspect.signature(route.fn)
    try:
        hints = typing.get_type_hints(route.fn)
    except Exception:  # noqa: BLE001
        hints = {}
    kinds = {int: {"type": "integer"}, float: {"type": "number"}}
    params, body = [], None
    for p in sig.parameters.values():
        if p.name == "request" or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if p.name == "body":
            body = schema_for(hints.get("body"), components) if hints.get("body") else {"type": "object"}
            continue
        if p.name in route.converters:
            convert = route.converters[p.name]
            params.append({"name": p.name, "in": "path", "required": True,
                           "schema": kinds.get(convert, {"type": "string"})})
        else:
            entry = {"name": p.name, "in": "query", "schema": schema_for(hints.get(p.name, p.annotation), components)}
            if p.default is p.empty:
                entry["required"] = True
            params.append(entry)
    doc = inspect.getdoc(route.fn) or ""
    op = {"operationId": f"{route.name}_{method.lower()}" if len(route.methods) > 2 else route.name,
          "summary": doc.splitlines()[0] if doc else route.name.replace("_", " "),
          "responses": {"200": {"description": "The result"}}}
    if params:
        op["parameters"] = params
    if body is not None and method in ("POST", "PUT", "PATCH"):
        op["requestBody"] = {"content": {"application/json": {"schema": body}}}
    if route.guard:
        op["security"] = [{"apiToken": []}, {"session": []}] if route.tokens else [{"session": []}]
        op["responses"]["401"] = {"description": "Not signed in", "content": {"application/json": {"schema": {
            "type": "object", "properties": {"error": {"type": "string"}}}}}}
    return op


def generate(app_path, *, server_url=None):
    """The OpenAPI document (a dict) for the app at ``app_path``."""
    from .app_loader import LoadedApp
    loaded = LoadedApp(app_path)
    components: dict = {}
    paths = {}
    for name in sorted(loaded.rpc):
        paths[f"/__pyweb/rpc/{name}"] = {"post": _operation(name, loaded.rpc[name], components)}
    for route in getattr(loaded.app, "http_routes", ()):
        item = paths.setdefault(_openapi_path(route.path), {})
        for method in route.methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            item[method.lower()] = _route_operation(route, method, components)
    from . import __version__
    doc = {
        "openapi": "3.1.0",
        "info": {"title": f"{loaded.title} API", "version": "1.0.0",
                 "description": f"Server functions of {loaded.title}, made with PyWeb {__version__}."},
        "paths": paths,
        "components": {
            "schemas": components,
            "securitySchemes": {
                "apiToken": {"type": "http", "scheme": "bearer", "description": "A token from /account/tokens"},
                "session": {"type": "apiKey", "in": "cookie", "name": "pyweb_session"},
            },
        },
    }
    if server_url:
        doc["servers"] = [{"url": server_url}]
    return doc
