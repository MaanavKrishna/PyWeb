"""PyWeb form validation (hardened): required, types, length caps, email."""
import re

MAX_FIELD_LENGTH = 500

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _check(value, rule: str) -> str | None:
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


def validate(data: dict, rules: dict) -> dict:
    """Validate ``data`` against ``{field: [rules]}``; return error dict."""
    errors: dict = {}
    for field, field_rules in rules.items():
        value = data.get(field)
        if isinstance(value, str) and len(value) > MAX_FIELD_LENGTH * 10:
            errors[field] = ["value too long"]
            continue
        for rule in field_rules:
            err = _check(value, rule)
            if err:
                errors.setdefault(field, []).append(err)
    return errors
