"""Minimal Model layer for the prototype (SQLite-backed, optional)."""

from __future__ import annotations

import sqlite3
import threading

_lock = threading.Lock()
_db_path = ":memory:"
_conns: dict[str, sqlite3.Connection] = {}


class Email(str):
    @classmethod
    def validate(cls, value):
        if "@" not in str(value):
            raise ValueError(f"invalid email: {value!r}")
        return cls(value)


class ModelMeta(type):
    _registry: list[type] = []

    def __new__(mcls, name, bases, ns):
        annotations = ns.get("__annotations__", {})
        cls = super().__new__(mcls, name, bases, ns)
        if name != "Model":
            cls._fields = dict(annotations)
            ModelMeta._registry.append(cls)
        return cls


class Model(metaclass=ModelMeta):
    _fields: dict = {}
    _table: str = ""

    @classmethod
    def _conn(cls):
        global _db_path
        key = _db_path
        with _lock:
            if key not in _conns:
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
