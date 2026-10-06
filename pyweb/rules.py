"""Validation rules: written once on a Model field, checked everywhere.

The same :class:`Rules` object checks a value when a Model is saved, when
an RPC argument arrives, when a form is posted, and (as the JSON from
:meth:`Rules.schema`) in the browser while the user types. Only these
declarative rules run in the browser; custom ``@validates`` methods run on
the server, so a browser check is never the only defence.

Messages are short phrases meant to follow the field's label:
"Title is required", "Name must be at most 80 characters".
"""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s.]{2,}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.I)

FORMATS = {"email": EMAIL_RE, "slug": SLUG_RE, "url": URL_RE}
FORMAT_MESSAGES = {"email": "must be a valid email address", "slug": "may only use a-z, 0-9 and single dashes",
                   "url": "must be a web address starting with http:// or https://"}

TEXT_KINDS = ("str", "text")
NUMBER_KINDS = ("int", "bigint", "float", "decimal")


class Rules:
    """What a valid value looks like. ``min``/``max`` bound numbers, the length of
    text and the size of lists; ``choices`` limits the value to a set; ``pattern``
    is a regular expression the whole text must match; ``format`` is ``email``,
    ``url`` or ``slug``."""

    __slots__ = ("required", "min", "max", "pattern", "choices", "format", "kind", "message", "_re")

    def __init__(self, *, kind="str", required=False, min=None, max=None, pattern=None,
                 choices=None, format=None, message=None):
        self.kind = kind
        self.required = required
        self.min = min
        self.max = max
        self.pattern = pattern
        self.choices = list(choices) if choices is not None else None
        if format is not None and format not in FORMATS:
            raise ValueError(f"unknown format {format!r} (use one of: {', '.join(FORMATS)})")
        self.format = format
        self.message = message
        self._re = re.compile(pattern) if pattern else None

    def __repr__(self):
        parts = [f"{k}={getattr(self, k)!r}" for k in ("kind", "required", "min", "max", "pattern", "choices", "format")
                 if getattr(self, k) not in (None, False)]
        return f"Rules({', '.join(parts)})"

    def check(self, value):
        """The first problem with ``value`` (a phrase like ``"is required"``), or None."""
        if value is None or (isinstance(value, str) and value == "" and self.kind in TEXT_KINDS):
            return (self.message or "is required") if self.required else None
        if isinstance(value, (list, tuple, set)):
            size, unit = len(value), ("item", "items")
        elif self.kind in TEXT_KINDS or isinstance(value, str):
            size, unit = len(str(value)), ("character", "characters")
        elif isinstance(value, (int, float)) and not isinstance(value, bool) or self.kind in NUMBER_KINDS:
            size, unit = value, None
        else:
            size, unit = None, None
        if size is not None:
            try:
                if self.min is not None and size < self.min:
                    return self.message or _bound("at least", self.min, unit)
                if self.max is not None and size > self.max:
                    return self.message or _bound("at most", self.max, unit)
            except TypeError:
                return self.message or "has the wrong type"
        if self.choices is not None and value not in self.choices:
            return self.message or ("must be one of: " + ", ".join(str(c) for c in self.choices))
        if self.format and isinstance(value, str) and not FORMATS[self.format].match(value):
            return self.message or FORMAT_MESSAGES[self.format]
        if self._re is not None and isinstance(value, str) and not self._re.fullmatch(value):
            return self.message or "has the wrong format"
        return None

    def schema(self):
        """The rules as JSON for the browser validator (only what is set)."""
        out = {"kind": self.kind}
        for key in ("required", "min", "max", "pattern", "choices", "format", "message"):
            val = getattr(self, key)
            if val not in (None, False):
                out[key] = val
        return out


def _bound(word, n, unit):
    if unit is None:
        return f"must be {word} {n}"
    return f"must be {word} {n} {unit[0] if n == 1 else unit[1]}"


class ValidationError(ValueError):
    """One or more fields are invalid: ``errors`` maps field name to message.

    RPC calls turn it into a 422 response with the errors per field, so a form
    shows each message next to its input.
    """

    def __init__(self, errors, message=None):
        if isinstance(errors, str):
            errors = {"__all__": errors}
        self.errors = dict(errors)
        # "is required" reads as "title is required"; a message that names its field ("Title is required") stays.
        super().__init__(message or "; ".join(f"{k} {v}" if k != "__all__" and str(v)[:1].islower() else str(v)
                                               for k, v in self.errors.items()))
