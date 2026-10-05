"""Model fields: one object holds a column's type, its schema and its rules.

A field written as a plain annotation (``title: str``) gets a :class:`Field`
automatically; ``Field(...)`` adds a default, constraints and validation
rules. On the class, a field is also a column expression for queries:
``Post.where(Post.views > 10)``.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import itertools
import json
import types
import typing
import uuid

from pyweb.rules import Rules

MISSING = type("MISSING", (), {"__repr__": lambda self: "MISSING", "__bool__": lambda self: False})()
_order = itertools.count()
UTC = _dt.timezone.utc


# ----------------------------------------------------------------- marker types

class Email(str):
    """Text that must be an email address."""

    @classmethod
    def validate(cls, value):
        from pyweb.rules import EMAIL_RE
        if not EMAIL_RE.match(str(value)):
            raise ValueError(f"invalid email: {value!r}")
        return cls(value)


class URL(str):
    """Text that must be an http(s) address."""


class Slug(str):
    """Lowercase letters, digits and single dashes (``my-first-post``)."""


class Text(str):
    """Long text (``TEXT``/``LONGTEXT``), with no length limit by default."""


class JSON:
    """Any JSON value (dicts, lists, numbers, text) stored in one column."""


_TYPE_KINDS = {int: "int", str: "str", float: "float", bool: "bool", decimal.Decimal: "decimal",
               _dt.datetime: "datetime", _dt.date: "date", _dt.time: "time", bytes: "bytes",
               uuid.UUID: "uuid", dict: "json", list: "json", JSON: "json",
               Email: "str", URL: "str", Slug: "str", Text: "text"}
_FORMATS = {Email: "email", URL: "url", Slug: "slug"}


def unwrap_optional(ann):
    """``(inner, optional)`` for ``X | None`` / ``Optional[X]``."""
    origin = typing.get_origin(ann)
    if origin is typing.Union or (getattr(types, "UnionType", None) is not None and isinstance(ann, types.UnionType)):
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        if len(args) == 1 and len(typing.get_args(ann)) == 2:
            return args[0], True
    return ann, False


def kind_for(ann):
    """The column kind for a Python annotation, or None if it isn't a scalar."""
    if ann in _TYPE_KINDS:
        return _TYPE_KINDS[ann]
    origin = typing.get_origin(ann)
    if origin in (dict, list) and ann is not None:
        return "json"
    if isinstance(ann, type):
        for base, kind in _TYPE_KINDS.items():
            if issubclass(ann, base) and base is not JSON:
                return kind
    return None


# ------------------------------------------------------------------- the field

class Field:
    """A column. ``Field(default, *, ...)``:

    * schema: ``primary_key``, ``unique``, ``index``, ``nullable``, ``column``
      (the database name if different), ``precision``/``scale`` for decimals.
    * rules: ``min``, ``max`` (number size, text length, list size), ``pattern``,
      ``choices``, ``format`` (``email``/``url``/``slug``), ``required``, ``message``.
    * behaviour: ``default_factory`` (called for each new row), ``auto_now_add`` /
      ``auto_now`` for timestamps, ``private=True`` (never accepted from a browser
      and left out of ``to_dict()``; e.g. password hashes), ``readonly=True``
      (never accepted from a browser, still shown).
    """

    legacy_kind = None       # set by the 0.4 IntegerField/TextField/... subclasses

    def __init__(self, default=MISSING, *, primary_key=False, nullable=None, unique=False, index=False,
                 min=None, max=None, pattern=None, choices=None, format=None, required=None, message=None,
                 label=None, help=None, column=None, precision=None, scale=None, kind=None,
                 default_factory=None, auto_now=False, auto_now_add=False, private=False, readonly=False):
        self.default = default
        self.default_factory = default_factory
        self.primary_key = primary_key
        self.nullable = nullable
        self.unique = unique
        self.index = index
        self.min, self.max, self.pattern, self.choices = min, max, pattern, choices
        self.format = format
        self.required = required
        self.message = message
        self.label = label
        self.help = help
        self.column = column
        self.precision, self.scale = precision, scale
        self.kind = kind or self.legacy_kind
        self.auto_now = auto_now
        self.auto_now_add = auto_now_add
        self.private = private
        self.readonly = readonly or primary_key or auto_now or auto_now_add
        self.name = ""
        self.model = None
        self.py_type = None
        self.autoincrement = False
        self.rules = None
        self._order = next(_order)

    # -- set up by the Model class -----------------------------------------
    def bind(self, model, name, annotation=MISSING):
        self.model = model
        self.name = name
        self.column = self.column or name
        if annotation is not MISSING and annotation is not None:
            inner, optional = unwrap_optional(annotation)
            self.py_type = inner
            if self.kind is None:
                self.kind = kind_for(inner)
            if self.format is None:
                for cls, fmt in _FORMATS.items():
                    if isinstance(inner, type) and issubclass(inner, cls):
                        self.format = fmt
            if self.nullable is None:
                self.nullable = optional
        if self.kind is None:
            self.kind = "str"
        if self.nullable is None:
            # 0.4 IntegerField()/TextField() columns without an annotation were nullable;
            # a column with no annotation and no explicit setting stays nullable too.
            self.nullable = annotation is MISSING
        if self.primary_key:
            self.nullable = False
        if self.required is None:
            self.required = (not self.nullable and not self.primary_key and self.default is MISSING
                             and self.default_factory is None and not (self.auto_now or self.auto_now_add)
                             and self.kind != "bool")
        self.rules = Rules(kind=self.kind, required=bool(self.required), min=self.min, max=self.max,
                           pattern=self.pattern, choices=self.choices, format=self.format, message=self.message)
        return self

    def __set_name__(self, owner, name):
        if not self.name:
            self.name = name

    # -- descriptor: Post.title is the column; post.title is the value -----
    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        try:
            return obj.__dict__[self.name]
        except KeyError:
            raise AttributeError(self.name) from None

    def __repr__(self):
        owner = getattr(self.model, "__name__", "?")
        return f"<Field {owner}.{self.name} {self.kind}>"

    # -- values -------------------------------------------------------------
    def initial(self):
        if self.default_factory is not None:
            return self.default_factory()
        if self.auto_now or self.auto_now_add:
            return _dt.datetime.now(UTC) if self.kind == "datetime" else _dt.date.today()
        if self.default is MISSING:
            return None
        if isinstance(self.default, (list, dict, set)):
            return type(self.default)(self.default)
        return self.default

    def db_default(self):
        """A constant default to put in the schema (only plain values)."""
        d = self.default
        if d is MISSING or d is None or callable(d) or isinstance(d, (list, dict, set)):
            return None
        if isinstance(d, (bool, int, float, str)):
            return d
        return None

    def to_db(self, value, dialect):
        if value is None:
            return None
        k, name = self.kind, dialect.name
        if k == "bool":
            return int(bool(value)) if name == "sqlite" else bool(value)
        if k in ("int", "bigint"):
            return int(value) if not isinstance(value, int) else value
        if k == "float":
            return float(value)
        if k == "decimal":
            v = value if isinstance(value, decimal.Decimal) else decimal.Decimal(str(value))
            return str(v) if name == "sqlite" else v
        if k == "datetime":
            if isinstance(value, str):
                value = _parse_datetime(value)
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            value = value.astimezone(UTC)
            if name == "sqlite":
                return value.isoformat()
            return value.replace(tzinfo=None) if name == "mysql" else value
        if k in ("date", "time"):
            if isinstance(value, str):
                return value
            return value.isoformat() if name == "sqlite" else value
        if k == "json":
            return json.dumps(value, separators=(",", ":"), default=str)
        if k == "uuid":
            return str(value)
        if k == "bytes":
            return bytes(value)
        if k in ("str", "text"):
            return str(value)
        return value

    def from_db(self, value):
        if value is None:
            return None
        k = self.kind
        try:
            if k == "bool":
                return bool(value)
            if k in ("int", "bigint"):
                return int(value)
            if k == "float":
                return float(value)
            if k == "decimal":
                return decimal.Decimal(str(value))
            if k == "datetime":
                if isinstance(value, str):
                    value = _parse_datetime(value)
                if isinstance(value, _dt.datetime):
                    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
                return value
            if k == "date":
                if isinstance(value, _dt.datetime):
                    return value.date()
                return _dt.date.fromisoformat(value) if isinstance(value, str) else value
            if k == "time":
                if isinstance(value, _dt.timedelta):      # MySQL drivers return TIME as a timedelta
                    return (_dt.datetime.min + value).time()
                return _dt.time.fromisoformat(value) if isinstance(value, str) else value
            if k == "json":
                if isinstance(value, (bytes, bytearray)):
                    value = value.decode()
                return json.loads(value) if isinstance(value, str) else value
            if k == "uuid":
                return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
            if k == "bytes":
                return bytes(value)
            if k in ("str", "text"):
                if isinstance(value, (bytes, bytearray)):
                    return value.decode()
                if self.py_type is not None and isinstance(self.py_type, type) and issubclass(self.py_type, str) \
                        and self.py_type is not str:
                    return self.py_type(value)
                return value
        except (ValueError, TypeError, decimal.InvalidOperation):
            return value
        return value

    def coerce(self, value):
        """``value`` as this field's Python type (for values from forms and JSON)."""
        if value is None or value == "" and self.kind not in ("str", "text"):
            return None
        k = self.kind
        if k == "bool":
            if isinstance(value, str):
                low = value.strip().lower()
                if low in ("1", "true", "yes", "on"):
                    return True
                if low in ("0", "false", "no", "off", ""):
                    return False
                raise ValueError("must be true or false")
            return bool(value)
        if k in ("int", "bigint"):
            if isinstance(value, bool):
                raise ValueError("must be a whole number")
            if isinstance(value, float) and not value.is_integer():
                raise ValueError("must be a whole number")
            try:
                return int(str(value).strip()) if isinstance(value, str) else int(value)
            except ValueError:
                raise ValueError("must be a whole number") from None
        if k == "float":
            try:
                return float(value)
            except (TypeError, ValueError):
                raise ValueError("must be a number") from None
        if k == "decimal":
            try:
                return decimal.Decimal(str(value).strip())
            except decimal.InvalidOperation:
                raise ValueError("must be a number") from None
        if k in ("datetime", "date", "time"):
            if isinstance(value, str):
                try:
                    if k == "datetime":
                        return _parse_datetime(value)
                    return _dt.date.fromisoformat(value) if k == "date" else _dt.time.fromisoformat(value)
                except ValueError:
                    raise ValueError(f"must be a valid {k}") from None
            return value
        if k in ("str", "text"):
            if isinstance(value, (dict, list)):
                raise ValueError("must be text")
            return str(value)
        if k == "uuid":
            try:
                return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
            except ValueError:
                raise ValueError("must be a valid id") from None
        return value

    def check(self, value):
        """The first rule ``value`` breaks, as a phrase, or None."""
        return self.rules.check(value) if self.rules is not None else None

    # -- query expressions ---------------------------------------------------
    def _cond(self, op, other):
        from .query import Compare
        return Compare(self, op, other)

    def __eq__(self, other):
        return self._cond("=", other)

    def __ne__(self, other):
        return self._cond("!=", other)

    def __lt__(self, other):
        return self._cond("<", other)

    def __le__(self, other):
        return self._cond("<=", other)

    def __gt__(self, other):
        return self._cond(">", other)

    def __ge__(self, other):
        return self._cond(">=", other)

    __hash__ = object.__hash__

    def in_(self, values):
        from .query import In
        return In(self, list(values))

    def not_in(self, values):
        from .query import In, Not
        return Not(In(self, list(values)))

    def is_null(self):
        from .query import Compare
        return Compare(self, "=", None)

    def not_null(self):
        from .query import Compare
        return Compare(self, "!=", None)

    def between(self, low, high):
        return (self >= low) & (self <= high)

    def like(self, pattern):
        from .query import Like
        return Like(self, pattern, ci=False, raw=True)

    def ilike(self, pattern):
        from .query import Like
        return Like(self, pattern, ci=True, raw=True)

    def contains(self, text):
        from .query import Like
        return Like(self, f"%{_escape_like(text)}%", ci=False)

    def icontains(self, text):
        from .query import Like
        return Like(self, f"%{_escape_like(text)}%", ci=True)

    def startswith(self, text):
        from .query import Like
        return Like(self, f"{_escape_like(text)}%", ci=False)

    def endswith(self, text):
        from .query import Like
        return Like(self, f"%{_escape_like(text)}", ci=False)

    def asc(self):
        from .query import Order
        return Order(self, desc=False)

    def desc(self):
        from .query import Order
        return Order(self, desc=True)


def _escape_like(text):
    return str(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_datetime(text):
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    return _dt.datetime.fromisoformat(text)


class Index:
    """An index on one or more fields: ``Index("author", "published_at")``,
    ``Index("email", unique=True)``. Listed in ``class Meta: indexes = [...]``."""

    def __init__(self, *fields, unique=False, name=None):
        if not fields:
            raise ValueError("Index needs at least one field")
        self.fields = fields
        self.unique = unique
        self.name = name


# --- 0.4 typed fields (still supported) ---------------------------------------

class IntegerField(Field):
    legacy_kind = "int"


class TextField(Field):
    legacy_kind = "str"


class RealField(Field):
    legacy_kind = "float"


class BlobField(Field):
    legacy_kind = "bytes"
