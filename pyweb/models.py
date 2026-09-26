"""Typed model layer for PyWeb (Track B)."""

from __future__ import annotations

from typing import Any, ClassVar


class Field:
    column_type = "TEXT"

    def __init__(self, *, primary_key=False, nullable=True, default=None):
        self.primary_key = primary_key
        self.nullable = nullable
        self.default = default
        self.name = ""

    def sql(self) -> str:
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

    def _default_sql(self) -> str:
        if isinstance(self.default, str):
            return "'" + self.default.replace("'", "''") + "'"
        if isinstance(self.default, bool):
            return "1" if self.default else "0"
        return str(self.default)

    def to_db(self, value: Any) -> Any:
        return value

    def from_db(self, value: Any) -> Any:
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
    def __new__(mcls, name, bases, ns):
        fields: dict[str, Field] = {}
        for base in bases:
            if hasattr(base, "__fields__"):
                fields.update(base.__fields__)
        for key, val in list(ns.items()):
            if isinstance(val, Field):
                val.name = key
                fields[key] = val
        ns["__fields__"] = fields
        ns.setdefault("__table__", name.lower())
        pks = [k for k, f in fields.items() if f.primary_key]
        ns["__pk__"] = pks[0] if pks else None
        return super().__new__(mcls, name, bases, ns)


class Model(metaclass=ModelMeta):
    __fields__: ClassVar[dict[str, Field]] = {}
    __table__: ClassVar[str] = ""
    __pk__: ClassVar[str | None] = None
    _db: ClassVar[Any] = None

    def __init__(self, **kwargs):
        for key, field in self.__fields__.items():
            setattr(self, key, kwargs.pop(key, field.default))
        if kwargs:
            raise TypeError(
                f"unknown fields for {type(self).__name__}: {sorted(kwargs)}"
            )

    @classmethod
    def bind(cls, db):
        cls._db = db
        return db

    @classmethod
    def _db_or_raise(cls):
        if cls._db is None:
            raise RuntimeError(
                f"{cls.__name__} is not bound to a database; "
                f"call {cls.__name__}.bind(db) first"
            )
        return cls._db

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__fields__}

    @classmethod
    def schema_sql(cls) -> str:
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
        data = {
            k: f.to_db(getattr(self, k))
            for k, f in self.__fields__.items()
            if not (f.primary_key and getattr(self, k) is None)
        }
        pk = self.__pk__
        if pk is not None and getattr(self, pk) is not None:
            assignments = {k: v for k, v in data.items() if k != pk}
            if assignments:
                q = Query(self.__table__).where(**{pk: getattr(self, pk)})
                sql, params = q.build_update(assignments)
                db.execute(sql, params)
        else:
            sql, params = Query(self.__table__).build_insert(data)
            result = db.execute(sql, params)
            if pk is not None and getattr(self, pk) is None:
                setattr(self, pk, result.lastrowid)
        return self

    @classmethod
    def create(cls, **kwargs):
        return cls(**kwargs).save()

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
    def all(cls):
        from pyweb.db import Query

        db = cls._db_or_raise()
        sql, params = Query(cls.__table__).build_select()
        res = db.execute(sql, params)
        return [cls._from_row(r, res.columns) for r in res.fetchall()]

    @classmethod
    def filter(cls, **kwargs):
        from pyweb.db import Query

        db = cls._db_or_raise()
        q = Query(cls.__table__).where(**kwargs)
        sql, params = q.build_select()
        res = db.execute(sql, params)
        return [cls._from_row(r, res.columns) for r in res.fetchall()]

    def delete(self):
        from pyweb.db import Query

        db = self._db_or_raise()
        pk = self.__pk__
        if pk is None or getattr(self, pk) is None:
            raise ValueError("cannot delete a model without a primary key value")
        q = Query(self.__table__).where(**{pk: getattr(self, pk)})
        sql, params = q.build_delete()
        db.execute(sql, params)
