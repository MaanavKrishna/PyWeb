"""Models: database tables as Python classes, on :mod:`pyweb.db`.

::

    from pyweb import Model, Field
    from pyweb.models import Text

    class User(Model):
        email: Email = Field(unique=True)
        name: str = Field(max=80)

    class Post(Model):
        title: str = Field(max=120)
        body: Text = ""
        author: User                     # a foreign key (column author_id)
        tags: list[Tag] = []             # many-to-many through posts_tags
        published_at: datetime | None = None

        class Meta:
            ordering = ["-published_at"]

    post = Post.create(title="Hello", author=user)
    recent = Post.where(Post.published_at.not_null()).include("author").limit(10)

Every Model gets an ``id`` primary key unless it declares one. Field rules
(``max=120``) are checked on ``save()`` and, through the same rule engine,
in RPC arguments, forms and the browser.

Where a Model's rows live, first match wins: ``Model.bind(db)`` on the class,
``App(database=...)``, the ``DATABASE_URL`` variable, and while developing an
in-memory SQLite database whose tables are created on first use.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import typing
import warnings
import weakref
from typing import ClassVar

from pyweb.rules import ValidationError

from .fields import (JSON, MISSING, URL, BlobField, Email, Field, Index, IntegerField, RealField, Slug, Text,
                     TextField, kind_for, unwrap_optional)
from .query import Avg, Count, Max, Min, Order, Page, QuerySet, Sum
from .relations import (ForeignKey, ManyToMany, ManyToManyReverse, NotIncluded, OneToOne, RelatedList, Relation,
                        Reverse, plural, snake)

log = logging.getLogger("pyweb.models")

__all__ = ["Model", "Field", "Index", "ForeignKey", "OneToOne", "ManyToMany", "NotIncluded", "RelatedList",
           "QuerySet", "Page", "Count", "Sum", "Avg", "Min", "Max", "Email", "URL", "Slug", "Text", "JSON",
           "ValidationError", "validates", "use_database", "database", "ensure_tables", "all_models",
           "IntegerField", "TextField", "RealField", "BlobField"]

_lock = threading.RLock()
_by_name: dict = {}                 # class name -> latest Model class with that name
_pending: list = []                 # Model classes not finalized yet


# ------------------------------------------------------------------ databases

class _State:
    db = None
    env_db = None
    implicit = None


_state = _State()
_auto: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()   # db -> set of tables made on demand


def strict_relations():
    """Whether reading a relation that wasn't ``include()``-d is an error (while developing) or a warning."""
    flag = os.environ.get("PYWEB_STRICT_RELATIONS")
    if flag is not None:
        return flag.strip().lower() in ("1", "true", "yes", "on")
    return os.environ.get("PYWEB_ENV", "development").strip().lower() != "production"


def use_database(db, *, auto_create=False):
    """Make ``db`` (a database or URL) where Models without their own binding live.

    ``auto_create=True`` creates missing tables the first time each Model is
    used (prototype mode: what ``pyweb dev`` does for apps without a
    ``migrations/`` folder). Returns the database.
    """
    if isinstance(db, str):
        from pyweb.db import connect
        db = connect(db)
    _state.db = db
    if auto_create and db is not None:
        _auto.setdefault(db, set())
    return db


def database():
    """The default database for Models (``None`` if none is set up yet)."""
    if _state.db is not None:
        return _state.db
    url = os.environ.get("DATABASE_URL")
    if url:
        if _state.env_db is None or getattr(_state.env_db, "_from_url", None) != url:
            from pyweb.db import connect
            _state.env_db = connect(url)
            _state.env_db._from_url = url
        return _state.env_db
    return None


def _implicit_db(model):
    if os.environ.get("PYWEB_ENV", "").strip().lower() == "production":
        raise RuntimeError(f"{model.__name__} is not bound to a database: set DATABASE_URL, "
                           f"pass App(database=...), or call {model.__name__}.bind(db)")
    with _lock:
        if _state.implicit is None:
            from pyweb.db import SQLiteDB
            _state.implicit = SQLiteDB(":memory:")
            _auto.setdefault(_state.implicit, set())
            log.info("Models are using an in-memory SQLite database (set DATABASE_URL or App(database=...) to keep data)")
        return _state.implicit


def ensure_tables(db=None, models=None):
    """Create the tables (and join tables) for ``models`` that ``db`` doesn't have yet.

    Never changes existing tables: use migrations (``pyweb db diff``) for that.
    """
    from pyweb.db import schema as S
    models = list(models) if models is not None else all_models()
    db = db or database()
    if db is None:
        raise RuntimeError("no database to create tables in")
    closure = _closure(models)
    return S.create_missing(db, S.from_models(closure))


def _closure(models):
    """``models`` plus every Model they point at (so foreign keys have their targets)."""
    out, todo = [], list(models)
    while todo:
        m = todo.pop()
        if m in out or m._meta.abstract:
            continue
        out.append(m)
        for rel in m._meta.relations.values():
            todo.append(rel.target)
        for rel in m._meta.m2m.values():
            todo.append(rel.target)
    return out


def all_models():
    """Every concrete Model class defined so far (the latest class for each name)."""
    _finalize_pending()
    seen, out = set(), []
    for model in ModelMeta._registry:
        if model._meta.abstract or _by_name.get(model.__name__) is not model:
            continue
        if model._meta.table in seen:
            continue
        seen.add(model._meta.table)
        out.append(model)
    return out


def resolve_model(ref, owner=None):
    """A Model class from a class, a name, or a string annotation."""
    if isinstance(ref, type) and issubclass(ref, Model):
        return ref
    if isinstance(ref, str):
        name = ref.strip().strip("'\"")
        if owner is not None:
            if name in ("self", "Self", owner.__name__):
                return owner
            obj = (owner.__dict__.get("_pyweb_globals") or {}).get(name)
            if isinstance(obj, type) and issubclass(obj, Model):
                return obj
        found = _by_name.get(name.rsplit(".", 1)[-1])
        if found is not None:
            return found
        raise LookupError(f"no Model named {name!r}" + (f" (used by {owner.__name__})" if owner else ""))
    raise TypeError(f"{ref!r} is not a Model")


# ------------------------------------------------------------------ validators

def validates(*names):
    """Mark a method as a validator for fields: it gets the value and raises
    ``ValueError("message")`` when it's wrong. Runs on the server only.

    ::

        @validates("username")
        def no_admin(self, value):
            if value.lower() == "admin":
                raise ValueError("is reserved")
    """
    def deco(fn):
        fn.__pyweb_validates__ = names
        return fn
    return deco


# -------------------------------------------------------------------- metadata

class ModelInfo:
    """Everything PyWeb knows about a Model class (``Post._meta``)."""

    def __init__(self, model, declared, options):
        self.model = model
        self.declared = declared              # name -> (value, annotation)
        self.options = options
        self.abstract = bool(options.get("abstract"))
        self._table = options.get("table")
        self.ready = False
        self.fields: dict = {}                # name -> Field (columns, including foreign key ids)
        self.relations: dict = {}             # name -> ForeignKey
        self.m2m: dict = {}                   # name -> ManyToMany
        self.reverse: dict = {}               # name -> Reverse / ManyToManyReverse
        self.pk = None
        self.indexes = list(options.get("indexes") or ())
        self.ordering = list(options.get("ordering") or ())
        self.validators: dict = {}
        self.policy = None
        self.legacy = False

    @property
    def table(self):
        legacy = self.model.__dict__.get("_table")
        if isinstance(legacy, str) and legacy:
            return legacy
        return self._table

    @table.setter
    def table(self, value):
        self._table = value

    def columns(self):
        return list(self.fields.values())

    def field(self, name):
        found = self.fields.get(name)
        if found is not None:
            return found
        rel = self.relations.get(name)
        if rel is not None:
            return rel.field
        for f in self.fields.values():
            if f.column == name:
                return f
        raise ValueError(f"{self.model.__name__} has no field {name!r} (fields: {', '.join(self.fields)})")

    def assignable(self, name):
        return self.field(name)

    def relation(self, name):
        rel = self.relations.get(name) or self.m2m.get(name) or self.reverse.get(name)
        if rel is None:
            names = list(self.relations) + list(self.m2m) + list(self.reverse)
            raise ValueError(f"{self.model.__name__} has no relation {name!r}"
                             + (f" (relations: {', '.join(names)})" if names else " (it has no relations)"))
        return rel

    def default_order(self):
        out = []
        for key in self.ordering:
            if isinstance(key, Order):
                out.append(key)
            else:
                out.append(Order(self.field(key.lstrip("-+")), desc=key.startswith("-")))
        return out

    def read_scope(self):
        if self.policy is None:
            return None
        return self.policy.read_condition(self.model)


def _finalize_pending():
    with _lock:
        for model in list(_pending):
            try:
                model._meta_finalize()
            except LookupError:
                continue          # a target defined later; tried again on next use


def _resolve_annotation(model, ann, globalns):
    if not isinstance(ann, str):
        return ann
    # The class itself, then the module that defines it, then any Model with that name.
    ns = dict(_by_name)
    ns.update(globalns or {})
    ns[model.__name__] = model
    try:
        return eval(ann, ns)  # noqa: S307 - the app's own annotation text
    except NameError as exc:
        missing = str(exc).split("'")[1] if "'" in str(exc) else ann
        raise LookupError(f"{model.__name__}: unknown name {missing!r} in annotation {ann!r}") from None


def _is_model_type(ann):
    return isinstance(ann, type) and issubclass(ann, Model) and ann is not Model


def _list_of_model(ann):
    if typing.get_origin(ann) in (list, set, tuple):
        args = typing.get_args(ann)
        if args and (_is_model_type(args[0]) or isinstance(args[0], (str, typing.ForwardRef))):
            inner = args[0]
            if isinstance(inner, typing.ForwardRef):
                inner = inner.__forward_arg__
            if isinstance(inner, str):
                return inner if inner in _by_name else None
            return inner
    return None


class ModelMeta(type):
    _registry: list = []

    def __new__(mcls, name, bases, ns, **kw):
        annotations = dict(ns.get("__annotations__", {}))
        meta_opts = {}
        inner_meta = ns.pop("Meta", None)
        if inner_meta is not None:
            meta_opts = {k: v for k, v in vars(inner_meta).items() if not k.startswith("__")}
        if ns.get("__abstract__"):
            meta_opts["abstract"] = True
        legacy_table = ns.get("__table__")
        declared: dict = {}
        for base in reversed(bases):
            base_meta = base.__dict__.get("_pyweb_meta")
            if base_meta is not None:
                declared.update(base_meta.declared)
        for key in list(annotations):
            if key.startswith("_") or typing.get_origin(annotations[key]) is ClassVar or \
                    (isinstance(annotations[key], str) and annotations[key].startswith(("ClassVar", "typing.ClassVar"))):
                continue
            declared[key] = (ns.get(key, MISSING), annotations[key])
        for key, value in list(ns.items()):
            if isinstance(value, (Field, Relation)) and key not in declared:
                declared[key] = (value, MISSING)
        cls = super().__new__(mcls, name, bases, ns, **kw)
        if name == "Model" and not bases[:1] == (object,) and ns.get("__module__") == __name__:
            return cls
        try:
            frame_globals = sys._getframe(1).f_globals
        except (AttributeError, ValueError):
            frame_globals = {}
        type.__setattr__(cls, "_pyweb_globals", frame_globals)
        table = meta_opts.get("table") or legacy_table
        legacy = any(isinstance(v, Field) and v.legacy_kind for v, _ in declared.values())
        if not table:
            table = name.lower() if legacy else plural(snake(name))
        meta_opts["table"] = table
        info = ModelInfo(cls, declared, meta_opts)
        info.legacy = legacy
        type.__setattr__(cls, "_pyweb_meta", info)
        # Columns exist on the class right away, so `Post.title > ...` works at import time.
        for key, (value, ann) in declared.items():
            if isinstance(value, (Field, Relation)):
                type.__setattr__(cls, key, value)
            elif key not in ns or not callable(value) or isinstance(value, (list, dict)):
                placeholder = Field(default=value)
                placeholder.name = key
                placeholder.model = cls
                placeholder.column = key
                type.__setattr__(cls, key, placeholder)
        for attr, value in ns.items():
            names = getattr(value, "__pyweb_validates__", None)
            if names:
                for fname in names:
                    info.validators.setdefault(fname, []).append(value)
        if not info.abstract:
            ModelMeta._registry.append(cls)
            _by_name[name] = cls
            _pending.append(cls)
        return cls

    # -- class-level properties (finalize on first use) ----------------------
    @property
    def _meta(cls):
        info = cls.__dict__.get("_pyweb_meta")
        if info is None:
            raise AttributeError("_meta")
        if not info.ready and not info.abstract:
            _finalize_pending()
            if not info.ready:
                cls._meta_finalize()
        return info

    @property
    def __fields__(cls):
        info = cls.__dict__.get("_pyweb_meta")
        if info is None:
            return {}
        return dict(cls._meta.fields)

    @property
    def __table__(cls):
        info = cls.__dict__.get("_pyweb_meta")
        return info.table if info is not None else ""

    @__table__.setter
    def __table__(cls, value):
        cls.__dict__["_pyweb_meta"].table = value

    @property
    def __pk__(cls):
        info = cls.__dict__.get("_pyweb_meta")
        if info is None:
            return None
        pk = cls._meta.pk
        return pk.name if pk is not None else None

    @property
    def _fields(cls):
        """The annotations the class declared (0.4 forms API)."""
        info = cls.__dict__.get("_pyweb_meta")
        if info is None:
            return {}
        out = {}
        for key, (value, ann) in info.declared.items():
            if ann is MISSING or isinstance(value, Relation):
                continue
            if isinstance(ann, str):
                try:
                    ann = _resolve_annotation(cls, ann, cls.__dict__.get("_pyweb_globals"))
                except LookupError:
                    pass
            out[key] = ann
        return out

    def _meta_finalize(cls):
        info = cls.__dict__["_pyweb_meta"]
        if info.ready:
            return
        with _lock:
            if info.ready:
                return
            globalns = cls.__dict__.get("_pyweb_globals") or {}
            resolved = {}
            for key, (value, ann) in info.declared.items():
                resolved[key] = (value, _resolve_annotation(cls, ann, globalns) if ann is not MISSING else MISSING)
            fields, relations, m2m = {}, {}, {}
            pk = None
            for key, (value, ann) in resolved.items():
                inner, _optional = unwrap_optional(ann) if ann is not MISSING else (MISSING, False)
                if isinstance(value, ManyToMany) or (not isinstance(value, (Field, Relation))
                                                     and ann is not MISSING and _list_of_model(ann) is not None):
                    rel = value if isinstance(value, ManyToMany) else ManyToMany(_list_of_model(ann))
                    rel.bind(cls, key, ann)
                    m2m[key] = rel
                    type.__setattr__(cls, key, rel)
                    continue
                if isinstance(value, ForeignKey) or (not isinstance(value, (Field, Relation))
                                                     and _is_model_type(inner)):
                    rel = value if isinstance(value, ForeignKey) else ForeignKey(
                        inner, on_delete="set null" if _optional else "cascade")
                    if rel.to is None and isinstance(inner, type):
                        rel.to = inner
                    rel.bind(cls, key, ann)
                    relations[key] = rel
                    fields[rel.field.name] = rel.field
                    type.__setattr__(cls, key, rel)
                    type.__setattr__(cls, rel.field.name, rel.field)
                    continue
                field = value if isinstance(value, Field) else Field(default=value)
                field.bind(cls, key, ann)
                if field.kind is None or (ann is not MISSING and kind_for(inner) is None
                                          and not isinstance(value, Field) and inner is not MISSING):
                    raise TypeError(f"{cls.__name__}.{key}: {ann!r} can't be stored in a column "
                                    f"(use int, str, float, bool, Decimal, datetime, date, dict/list (JSON), a Model...)")
                fields[key] = field
                type.__setattr__(cls, key, field)
                if field.primary_key:
                    pk = field
            if pk is None:
                pk = Field(primary_key=True, kind="bigint")
                pk.bind(cls, "id", MISSING)
                pk.autoincrement = True
                fields = {"id": pk, **fields}
                type.__setattr__(cls, "id", pk)
            elif pk.kind in ("int", "bigint"):
                pk.autoincrement = True
            info.fields = fields
            info.relations = relations
            info.m2m = m2m
            info.pk = pk
            info.ready = True
            if cls in _pending:
                _pending.remove(cls)
            # reverse accessors on the targets
            for key, rel in relations.items():
                target = rel.target
                rname = rel.related_name or (snake(cls.__name__) if isinstance(rel, OneToOne) else plural(snake(cls.__name__)))
                if rname in target.__dict__ and not isinstance(target.__dict__[rname], (Reverse, ManyToManyReverse)):
                    raise TypeError(f"{cls.__name__}.{key}: {target.__name__} already has {rname!r}; "
                                    f"pass ForeignKey(related_name=...)")
                rev = Reverse(rel)
                rev.name = rname
                rev.model = target
                target.__dict__["_pyweb_meta"].reverse[rname] = rev
                type.__setattr__(target, rname, rev)
            for key, rel in m2m.items():
                target = rel.target
                rname = rel.related_name or plural(snake(cls.__name__))
                if target is cls and not rel.related_name:
                    continue                 # self-links need a related_name for the other direction
                rev = ManyToManyReverse(rel)
                rev.name = rname
                rev.model = target
                target.__dict__["_pyweb_meta"].reverse[rname] = rev
                type.__setattr__(target, rname, rev)


# ------------------------------------------------------------------------ Model

class Model(metaclass=ModelMeta):
    """Base class for database Models. See the module docs."""

    _db: ClassVar = None

    # -- construction --------------------------------------------------------
    def __init__(self, **kwargs):
        meta = type(self)._meta
        d = self.__dict__
        d["_persisted"] = False
        d["_orig"] = {}
        for f in meta.fields.values():
            if f.name in kwargs:
                d[f.name] = kwargs.pop(f.name)
            elif f.column != f.name and f.column in kwargs:
                d[f.name] = kwargs.pop(f.column)
            else:
                d[f.name] = f.initial()
        for name, rel in meta.relations.items():
            if name in kwargs:
                rel.__set__(self, kwargs.pop(name))
        for name in list(meta.m2m):
            if name in kwargs:
                d.setdefault("_pending_m2m", {})[name] = list(kwargs.pop(name))
        if kwargs:
            raise TypeError(f"unknown fields for {type(self).__name__}: {sorted(kwargs)}")

    @classmethod
    def _from_row(cls, row, columns, db=None):
        meta = cls._meta
        obj = cls.__new__(cls)
        d = obj.__dict__
        by_column = {f.column: f for f in meta.fields.values()}
        for col, val in zip(columns, row):
            f = by_column.get(col)
            if f is not None:
                d[f.name] = f.from_db(val)
            else:
                d[col] = val
        for f in meta.fields.values():
            d.setdefault(f.name, None)
        d["_persisted"] = True
        d["_db"] = db
        d["_orig"] = _snapshot(meta, d)
        return obj

    # -- identity --------------------------------------------------------------
    @property
    def pk(self):
        return self.__dict__.get(type(self)._meta.pk.name)

    def __eq__(self, other):
        if type(other) is not type(self):
            return NotImplemented
        if self.pk is None or other.pk is None:
            return self is other
        return self.pk == other.pk

    def __hash__(self):
        return hash((type(self).__name__, self.pk)) if self.pk is not None else id(self)

    def __repr__(self):
        meta = type(self)._meta
        shown = []
        for f in list(meta.fields.values())[:4]:
            if not f.private:
                shown.append(f"{f.name}={self.__dict__.get(f.name)!r}")
        return f"<{type(self).__name__} {' '.join(shown)}>"

    # 0.4 code treated rows as dicts: row["name"], dict(row), row.get("name").
    def __getitem__(self, key):
        meta = type(self)._meta
        if key in meta.fields or key in meta.relations:
            return getattr(self, key)
        raise KeyError(key)

    def keys(self):
        return [f.name for f in type(self)._meta.fields.values() if not f.private]

    def __contains__(self, key):
        return key in type(self)._meta.fields

    def to_dict(self, *, include_private=False):
        """The row as plain data, with the related rows that were loaded."""
        meta = type(self)._meta
        d = self.__dict__
        out = {f.name: d.get(f.name) for f in meta.fields.values() if include_private or not f.private}
        for name in list(meta.relations) + list(meta.m2m) + list(meta.reverse):
            loaded = d.get("__rel_" + name, MISSING)
            if loaded is MISSING:
                continue
            if isinstance(loaded, RelatedList):
                if loaded._loaded:
                    out[name] = [item.to_dict() for item in list.__iter__(loaded)]
            elif loaded is None:
                out[name] = None
            else:
                out[name] = loaded.to_dict()
        return out

    # -- database --------------------------------------------------------------
    @classmethod
    def bind(cls, db):
        """Store this Model's rows in ``db`` (overrides the default database)."""
        cls._db = db
        return db

    @classmethod
    def _database(cls):
        db = cls._db
        if db is None:
            db = database() or _implicit_db(cls)
        made = _auto.get(db)
        if made is not None:
            table = cls._meta.table
            if table not in made:
                with _lock:
                    if table not in made:
                        tables = [m._meta.table for m in _closure([cls])]
                        ensure_tables(db, [cls])
                        made.update(tables)
                        for m in cls._meta.m2m.values():
                            made.add(m.table)
        return db

    def _db_used(self):
        return self.__dict__.get("_db") or type(self)._database()

    @classmethod
    def _db_or_raise(cls):
        return cls._database()

    @classmethod
    def query(cls):
        """A query over every row (refine it with ``where``, ``order``...)."""
        return QuerySet(cls)

    @classmethod
    def where(cls, *conds, **kwargs):
        return QuerySet(cls).where(*conds, **kwargs)

    @classmethod
    def filter(cls, *conds, **kwargs):
        return QuerySet(cls).where(*conds, **kwargs)

    @classmethod
    def exclude(cls, *conds, **kwargs):
        return QuerySet(cls).exclude(*conds, **kwargs)

    @classmethod
    def order(cls, *keys):
        return QuerySet(cls).order(*keys)

    @classmethod
    def include(cls, *names):
        return QuerySet(cls).include(*names)

    @classmethod
    def all(cls):
        """Every row, as a list."""
        return QuerySet(cls).all()

    @classmethod
    def first(cls):
        return QuerySet(cls).first()

    @classmethod
    def count(cls):
        return QuerySet(cls).count()

    @classmethod
    def exists(cls):
        return QuerySet(cls).exists()

    @classmethod
    def get(cls, *conds, **kwargs):
        """The row with this primary key (``Post.get(3)``) or matching the conditions; None if missing."""
        return QuerySet(cls).get(*conds, **kwargs)

    @classmethod
    def get_or_404(cls, *conds, **kwargs):
        return QuerySet(cls).get_or_404(*conds, **kwargs)

    @classmethod
    def page(cls, size=20, after=None):
        return QuerySet(cls).page(size, after)

    @classmethod
    def create(cls, **values):
        """Insert a row and return it."""
        return cls(**values).save()

    @classmethod
    def get_or_create(cls, defaults=None, **match):
        """``(row, created)``: the row matching ``match``, or a new one with ``defaults`` too."""
        found = QuerySet(cls).where(**match).first()
        if found is not None:
            return found, False
        try:
            return cls.create(**match, **(defaults or {})), True
        except ValidationError:
            found = QuerySet(cls).where(**match).first()        # another request created it first
            if found is None:
                raise
            return found, False

    @classmethod
    def upsert(cls, *, key, **values):
        """Insert, or update the row whose ``key`` field(s) match. Returns nothing."""
        from pyweb.db.dialect import dialect_of
        meta = cls._meta
        db = cls._database()
        d = dialect_of(db)
        keys = [key] if isinstance(key, str) else list(key)
        obj = cls(**values)
        for f in meta.fields.values():
            if f.auto_now:
                obj.__dict__[f.name] = f.initial()
        obj.validate()
        data = {f.column: f.to_db(obj.__dict__.get(f.name), d) for f in meta.fields.values()
                if not (f.primary_key and obj.__dict__.get(f.name) is None)}
        key_cols = [meta.field(k).column for k in keys]
        update_cols = [c for c in data if c not in key_cols and c != meta.pk.column]
        update_cols = [c for c in update_cols if not any(f.column == c and f.auto_now_add for f in meta.fields.values())]
        db.execute(d.upsert_sql(meta.table, list(data), key_cols, update_cols), list(data.values()))

    @classmethod
    def bulk_create(cls, rows):
        """Insert many rows in one transaction; returns them (with ids)."""
        db = cls._database()
        objs = [r if isinstance(r, cls) else cls(**r) for r in rows]
        with db.transaction():
            for obj in objs:
                obj.save()
        return objs

    @classmethod
    def create_table(cls, db=None):
        """Create this Model's table (and join tables) if missing."""
        return ensure_tables(db or cls._database(), [cls])

    @classmethod
    def schema_sql(cls, dialect=None):
        """The ``CREATE TABLE`` statement for this Model."""
        from pyweb.db import schema as S
        from pyweb.db.dialect import SQLITE, dialect_of
        d = dialect or (dialect_of(cls._db) if cls._db is not None else SQLITE)
        table = S.from_models([cls])[cls._meta.table]
        return S.create_table_sql(table, d)[0].replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1)

    # -- 0.4 compatibility ------------------------------------------------------
    @classmethod
    def table(cls):
        return cls._meta.table

    @classmethod
    def migrate(cls):
        """0.4: create the table if missing. Use migrations (``pyweb db diff``) instead."""
        return cls.create_table()

    @classmethod
    def configure(cls, path=":memory:"):
        """0.4: keep every Model in the SQLite file ``path``. Use ``App(database=...)`` instead."""
        warnings.warn("Model.configure() is deprecated: use App(database='sqlite:///app.db') or DATABASE_URL",
                      DeprecationWarning, stacklevel=2)
        from pyweb.db import SQLiteDB
        use_database(SQLiteDB(path), auto_create=True)

    # -- validation ----------------------------------------------------------------
    def validate(self):
        """Check every field's rules and ``@validates`` methods; raise :class:`ValidationError`."""
        meta = type(self)._meta
        d = self.__dict__
        errors = {}
        for f in meta.fields.values():
            if f.primary_key:
                continue
            value = d.get(f.name)
            rel = getattr(f, "relation", None)
            if rel is not None:
                cached = d.get("__rel_" + rel.name)
                if value is None and cached is not None and cached.pk is not None:
                    value = d[f.name] = cached.pk
                if value is None and not f.nullable:
                    errors[rel.name] = f.message or "is required"
                continue
            if value is not None and not (f.auto_now or f.auto_now_add):
                try:
                    value = f.coerce(value)
                    d[f.name] = value
                except (ValueError, TypeError) as exc:
                    errors[f.name] = str(exc) or "has the wrong type"
                    continue
            problem = f.check(value)
            if problem is None and value is None and not f.nullable and not (f.auto_now or f.auto_now_add):
                problem = "is required"
            if problem:
                errors[f.name] = problem
                continue
            for fn in meta.validators.get(f.name, ()):
                try:
                    fn(self, value)
                except ValueError as exc:
                    errors[f.name] = str(exc)
                    break
        clean = getattr(self, "clean", None)
        if callable(clean) and not errors:
            try:
                clean()
            except ValidationError as exc:
                errors.update(exc.errors)
            except ValueError as exc:
                errors["__all__"] = str(exc)
        if errors:
            raise ValidationError(errors)

    # -- writing -----------------------------------------------------------------
    def save(self, *, validate=True):
        """Insert or update this row (only changed columns are written). Returns it."""
        from pyweb.db.dialect import dialect_of
        cls = type(self)
        meta = cls._meta
        db = self._db_used()
        d = dialect_of(db)
        state = self.__dict__
        for name, rel in meta.relations.items():
            cached = state.get("__rel_" + name)
            if cached is not None and state.get(rel.field.name) is None:
                if cached.pk is None:
                    raise ValueError(f"save the {type(cached).__name__} before saving this {cls.__name__}")
                state[rel.field.name] = cached.pk
        for f in meta.fields.values():
            if f.auto_now and state.get("_persisted"):
                state[f.name] = f.initial()
        if validate:
            self.validate()
        pk = meta.pk
        q = d.quote
        try:
            if not state.get("_persisted"):
                data = {f.column: f.to_db(state.get(f.name), d) for f in meta.fields.values()
                        if not (f.primary_key and state.get(f.name) is None)}
                if data:
                    sql = (f"INSERT INTO {q(meta.table)} ({', '.join(q(c) for c in data)}) "
                           f"VALUES ({', '.join('?' for _ in data)})")
                else:
                    sql = f"INSERT INTO {q(meta.table)} DEFAULT VALUES" if d.name != "mysql" else \
                        f"INSERT INTO {q(meta.table)} () VALUES ()"
                if d.supports_returning and state.get(pk.name) is None:
                    res = db.execute(sql + f" RETURNING {q(pk.column)}", list(data.values()))
                    state[pk.name] = pk.from_db(res.fetchone()[0])
                else:
                    res = db.execute(sql, list(data.values()))
                    if state.get(pk.name) is None:
                        state[pk.name] = res.lastrowid
                state["_persisted"] = True
                state["_db"] = db
            else:
                orig = state.get("_orig", {})
                changed = {f.column: f.to_db(state.get(f.name), d) for f in meta.fields.values()
                           if not f.primary_key and state.get(f.name) != orig.get(f.name, MISSING)}
                if changed:
                    sql = (f"UPDATE {q(meta.table)} SET {', '.join(f'{q(c)} = ?' for c in changed)} "
                           f"WHERE {q(pk.column)} = ?")
                    db.execute(sql, [*changed.values(), pk.to_db(self.pk, d)])
        except Exception as exc:  # noqa: BLE001 - only unique violations are translated
            field = _unique_violation(exc, meta)
            if field is not None:
                raise ValidationError({field: "is already taken"}) from exc
            raise
        state["_orig"] = _snapshot(meta, state)
        pending = state.pop("_pending_m2m", None)
        if pending:
            for name, items in pending.items():
                getattr(self, name).set(items)
        return self

    def delete(self):
        """Delete this row (rows that point at it follow their ``on_delete`` rule)."""
        from pyweb.db.dialect import dialect_of
        meta = type(self)._meta
        if self.pk is None:
            raise ValueError("cannot delete a model without a primary key value")
        db = self._db_used()
        d = dialect_of(db)
        db.execute(f"DELETE FROM {d.quote(meta.table)} WHERE {d.quote(meta.pk.column)} = ?",
                   [meta.pk.to_db(self.pk, d)])
        self.__dict__["_persisted"] = False

    def refresh(self):
        """Reload this row's columns from the database."""
        fresh = type(self).query().using(self._db_used()).get(self.pk)
        if fresh is None:
            raise LookupError(f"{type(self).__name__} {self.pk} no longer exists")
        meta = type(self)._meta
        for f in meta.fields.values():
            self.__dict__[f.name] = fresh.__dict__.get(f.name)
        self.__dict__["_orig"] = dict(fresh.__dict__["_orig"])
        return self

    def update(self, **values):
        """Set fields and save: ``post.update(title="New")``."""
        meta = type(self)._meta
        for key, value in values.items():
            if key in meta.relations or key in meta.m2m:
                setattr(self, key, value)
            elif key in meta.fields:
                self.__dict__[key] = value
            else:
                raise TypeError(f"unknown field for {type(self).__name__}: {key!r}")
        return self.save()


def _snapshot(meta, state):
    """Column values as saved (JSON values copied, so changes made in place are noticed)."""
    import copy
    return {f.name: copy.deepcopy(state.get(f.name)) if f.kind == "json" else state.get(f.name)
            for f in meta.fields.values()}


def _unique_violation(exc, meta):
    """The field a unique-constraint error is about, or None."""
    import re
    text = str(exc)
    code = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    errno = getattr(exc, "errno", None) or (exc.args[0] if exc.args and isinstance(exc.args[0], int) else None)
    if not ("UNIQUE constraint failed" in text or code == "23505" or errno == 1062 or "Duplicate entry" in text):
        return None
    columns = {f.column: f.name for f in meta.fields.values()}
    m = re.search(r"UNIQUE constraint failed: ([\w.]+(?:, [\w.]+)*)", text)
    if m:
        col = m.group(1).split(",")[0].split(".")[-1].strip()
        return columns.get(col, col)
    m = re.search(r"Key \(([^)]+)\)", text) or re.search(r"uq_\w*?_(\w+)'?", text)
    if m:
        col = m.group(1).split(",")[0].strip()
        for c, name in columns.items():
            if col == c or col.endswith(c):
                return name
        return col
    return "__all__"
