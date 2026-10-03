"""Model layer: annotation-style prototype models plus typed Field models
bound to a database driver (SQLite/Postgres)."""

from __future__ import annotations

import threading
from typing import ClassVar

_lock = threading.RLock()
_db_path = ":memory:"
_conns: dict = {}  # path -> sqlite3.Connection


class Email(str):
    @classmethod
    def validate(cls, value):
        if "@" not in str(value):
            raise ValueError(f"invalid email: {value!r}")
        return cls(value)


class Field:
    column_type = "TEXT"

    def __init__(self, *, primary_key=False, nullable=True, default=None):
        self.primary_key = primary_key
        self.nullable = nullable
        self.default = default
        self.name = ""

    def sql(self):
        parts = [self.name, self.column_type]
        if self.primary_key:
            parts.append("PRIMARY KEY")
            if self.column_type == "INTEGER":
                parts.append("AUTOINCREMENT")
        elif not self.nullable:
            parts.append("NOT NULL")
        if self.default is not None and not self.primary_key:
            parts.append(f"DEFAULT {self._default_sql()}")
        return " ".join(parts)

    def _default_sql(self):
        if isinstance(self.default, str):
            return "'" + self.default.replace("'", "''") + "'"
        if isinstance(self.default, bool):
            return "1" if self.default else "0"
        return str(self.default)

    def to_db(self, value):
        return value

    def from_db(self, value):
        return value


class IntegerField(Field):
    column_type = "INTEGER"


class TextField(Field):
    column_type = "TEXT"


class RealField(Field):
    column_type = "REAL"


class BlobField(Field):
    column_type = "BLOB"


class ModelMeta(type):
    _registry: list[type] = []

    def __new__(mcls, name, bases, ns):
        fields: dict[str, Field] = {}
        for base in bases:
            if hasattr(base, "__fields__") and isinstance(getattr(base, "__fields__"), dict):
                for k, f in getattr(base, "__fields__").items():
                    if isinstance(f, Field):
                        fields[k] = f
        annotations = ns.get("__annotations__", {})
        cls = super().__new__(mcls, name, bases, ns)
        if name != "Model":
            cls._fields = dict(annotations)
            for key, val in list(ns.items()):
                if isinstance(val, Field):
                    val.name = key
                    fields[key] = val
            if fields:
                cls.__fields__ = fields
                if "__table__" not in ns:
                    cls.__table__ = name.lower()
                pks = [k for k, f in fields.items() if f.primary_key]
                cls.__pk__ = pks[0] if pks else None
            elif "__fields__" not in ns:
                cls.__fields__ = {}
            ModelMeta._registry.append(cls)
        return cls


class Model(metaclass=ModelMeta):
    _fields: dict = {}
    _table: str = ""
    __fields__: ClassVar[dict] = {}
    __table__: ClassVar[str] = ""
    __pk__: ClassVar[str | None] = None
    _db: ClassVar = None

    def __init__(self, **kwargs):
        if getattr(type(self), "__fields__", {}):
            for key, field in type(self).__fields__.items():
                setattr(self, key, kwargs.pop(key, field.default))
            if kwargs:
                raise TypeError(f"unknown fields for {type(self).__name__}: {sorted(kwargs)}")
        else:
            for k, v in kwargs.items():
                setattr(self, k, v)

    @classmethod
    def bind(cls, db):
        cls._db = db
        return db

    @classmethod
    def _db_or_raise(cls):
        if cls._db is None:
            raise RuntimeError(f"{cls.__name__} is not bound to a database; "
                               f"call {cls.__name__}.bind(db) first")
        return cls._db

    def to_dict(self):
        if getattr(type(self), "__fields__", {}):
            return {k: getattr(self, k) for k in type(self).__fields__}
        return dict(self.__dict__)

    @classmethod
    def schema_sql(cls):
        cols = ", ".join(f.sql() for f in cls.__fields__.values())
        return f"CREATE TABLE IF NOT EXISTS {cls.__table__} ({cols})"

    @classmethod
    def _from_row(cls, row, columns):
        obj = cls.__new__(cls)
        for col, val in zip(columns, row):
            field = cls.__fields__.get(col)
            setattr(obj, col, field.from_db(val) if field else val)
        return obj

    def save(self):
        from pyweb.db import Query
        db = self._db_or_raise()
        data = {k: f.to_db(getattr(self, k)) for k, f in type(self).__fields__.items()
                if not (f.primary_key and getattr(self, k) is None)}
        pk = type(self).__pk__
        if pk is not None and getattr(self, pk) is not None:
            assignments = {k: v for k, v in data.items() if k != pk}
            if assignments:
                q = Query(type(self).__table__).where(**{pk: getattr(self, pk)})
                sql, params = q.build_update(assignments)
                db.execute(sql, params)
        else:
            sql, params = Query(type(self).__table__).build_insert(data)
            result = db.execute(sql, params)
            if pk is not None and getattr(self, pk) is None:
                setattr(self, pk, result.lastrowid)
        return self

    def delete(self):
        from pyweb.db import Query
        db = self._db_or_raise()
        pk = type(self).__pk__
        if pk is None or getattr(self, pk) is None:
            raise ValueError("cannot delete a model without a primary key value")
        q = Query(type(self).__table__).where(**{pk: getattr(self, pk)})
        sql, params = q.build_delete()
        db.execute(sql, params)

    @classmethod
    def get(cls, pk_value):
        from pyweb.db import Query
        db = cls._db_or_raise()
        q = Query(cls.__table__).where(**{cls.__pk__: pk_value})
        sql, params = q.build_select()
        res = db.execute(sql, params)
        rows = res.fetchall()
        if not rows:
            return None
        return cls._from_row(rows[0], res.columns)

    @classmethod
    def filter(cls, **kwargs):
        from pyweb.db import Query
        db = cls._db_or_raise()
        q = Query(cls.__table__).where(**kwargs)
        sql, params = q.build_select()
        res = db.execute(sql, params)
        return [cls._from_row(r, res.columns) for r in res.fetchall()]

    @classmethod
    def _conn(cls):
        global _db_path
        key = _db_path
        with _lock:
            if key not in _conns:
                import sqlite3  # imported on use: some Pythons (e.g. Pyodide) ship it separately
                _conns[key] = sqlite3.connect(key, check_same_thread=False)
                _conns[key].row_factory = sqlite3.Row
            return _conns[key]

    @classmethod
    def configure(cls, path=":memory:"):
        global _db_path
        _db_path = path

    @classmethod
    def table(cls):
        return cls._table or cls.__name__.lower() + "s"

    @classmethod
    def migrate(cls):
        cols = []
        for name, ann in cls._fields.items():
            tname = getattr(ann, "__name__", "TEXT").upper()
            sql_t = {"STR": "TEXT", "INT": "INTEGER", "FLOAT": "REAL", "BOOL": "INTEGER"}.get(tname, "TEXT")
            cols.append(f'"{name}" {sql_t}')
        conn = cls._conn()
        with _lock:
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{cls.table()}" (id INTEGER PRIMARY KEY AUTOINCREMENT, {", ".join(cols)})')
            conn.commit()

    @classmethod
    def create(cls, **kwargs):
        if getattr(cls, "__fields__", {}):
            return cls(**kwargs).save()
        cls.migrate()
        conn = cls._conn()
        keys = list(kwargs)
        with _lock:
            cur = conn.execute(
                f'INSERT INTO "{cls.table()}" ({", ".join(keys)}) VALUES ({", ".join("?" for _ in keys)})',
                [kwargs[k] for k in keys],
            )
            conn.commit()
            return {"id": cur.lastrowid, **kwargs}

    @classmethod
    def all(cls):
        if getattr(cls, "__fields__", {}):
            from pyweb.db import Query
            db = cls._db_or_raise()
            sql, params = Query(cls.__table__).build_select()
            res = db.execute(sql, params)
            return [cls._from_row(r, res.columns) for r in res.fetchall()]
        cls.migrate()
        conn = cls._conn()
        with _lock:
            return [dict(r) for r in conn.execute(f'SELECT * FROM "{cls.table()}"')]

    @classmethod
    def where(cls, **kwargs):
        cls.migrate()
        conn = cls._conn()
        with _lock:
            if not kwargs:
                return cls.all()
            cond = " AND ".join(f'"{k}" = ?' for k in kwargs)
            return [dict(r) for r in conn.execute(f'SELECT * FROM "{cls.table()}" WHERE {cond}', list(kwargs.values()))]
