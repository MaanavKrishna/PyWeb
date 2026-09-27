"""Forms: single-schema derivation from Model annotations, validation,
plus rule-based request validation."""

from __future__ import annotations

import html as _html
import re

MAX_FIELD_LENGTH = 500

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _check_rule(value, rule):
    name, _, arg = rule.partition(":")
    if name == "required":
        if value is None or (isinstance(value, str) and not value.strip()):
            return "this field is required"
    elif name == "email":
        if value is not None and not _EMAIL_RE.match(str(value)):
            return "enter a valid email address"
    elif name == "max_length":
        if value is not None and len(str(value)) > int(arg):
            return f"must be at most {arg} characters"
    elif name == "min_length":
        if value is not None and len(str(value)) < int(arg):
            return f"must be at least {arg} characters"
    return None


def validate_request(data, rules):
    """Validate ``data`` against ``{field: [rules]}``; return error dict."""
    errors = {}
    for field, field_rules in rules.items():
        value = data.get(field)
        if isinstance(value, str) and len(value) > MAX_FIELD_LENGTH * 10:
            errors[field] = ["value too long"]
            continue
        for rule in field_rules:
            err = _check_rule(value, rule)
            if err:
                errors.setdefault(field, []).append(err)
    return errors


def _kind(ann):
    name = getattr(ann, "__name__", str(ann))
    if name == "Email":
        return "email"
    return {"str": "text", "int": "number", "float": "number",
            "bool": "checkbox"}.get(name, "text")


def fields_for(model):
    fields = []
    for fname, ann in getattr(model, "_fields", {}).items():
        name = getattr(ann, "__name__", str(ann))
        fields.append({"name": fname, "kind": _kind(ann), "type": name,
                       "required": True})
    return fields


def render_form(model, *, action="", method="post", submit="Submit",
                csrf_token=None):
    parts = [f'<form action="{_html.escape(action)}" method="{method}">']
    if csrf_token:
        parts.append(f'<input type="hidden" name="csrf_token" '
                     f'value="{_html.escape(csrf_token)}" />')
    for f in fields_for(model):
        if f["kind"] == "checkbox":
            parts.append(f'<label><input type="checkbox" name="{f["name"]}" /> {f["name"]}</label>')
        else:
            parts.append(f'<label>{f["name"]}<input type="{f["kind"]}" name="{f["name"]}" required /></label>')
    parts.append(f"<button>{_html.escape(submit)}</button></form>")
    return "".join(parts)


def validate(model, data):
    """Validate a dict against model annotations. Returns (clean, errors)."""
    clean, errors = {}, {}
    import datetime
    from decimal import Decimal
    for fname, ann in getattr(model, "_fields", {}).items():
        name = getattr(ann, "__name__", str(ann))
        raw = (data or {}).get(fname)
        if raw in (None, ""):
            errors[fname] = "required"
            continue
        try:
            if name == "Email":
                if "@" not in str(raw):
                    raise ValueError("must be a valid email")
                clean[fname] = str(raw)
            elif name == "int":
                clean[fname] = int(raw)
            elif name == "float":
                clean[fname] = float(raw)
            elif name == "Decimal":
                clean[fname] = Decimal(str(raw))
            elif name == "bool":
                clean[fname] = str(raw).lower() in ("1", "true", "yes", "on")
            elif name == "datetime":
                clean[fname] = raw if isinstance(raw, datetime.datetime) else datetime.datetime.fromisoformat(str(raw))
            else:
                clean[fname] = str(raw)
        except (ValueError, TypeError, ArithmeticError) as exc:
            errors[fname] = str(exc)
    return clean, errors
