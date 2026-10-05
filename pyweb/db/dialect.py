"""SQL dialects: the few places SQLite, Postgres and MySQL differ.

Everything PyWeb generates (queries, DDL, migrations) goes through a
:class:`Dialect`, so one Model works on all three databases. Values are
never formatted into SQL: placeholders are always ``?`` and the driver
layer rewrites them for ``%s`` drivers.

Column *kinds* are PyWeb's portable types:

========  =================  =====================  ==================
kind      SQLite             Postgres               MySQL
========  =================  =====================  ==================
int       INTEGER            INTEGER                INT
bigint    INTEGER            BIGINT                 BIGINT
str       TEXT               VARCHAR(n) / TEXT      VARCHAR(n or 255)
text      TEXT               TEXT                   LONGTEXT
float     REAL               DOUBLE PRECISION       DOUBLE
bool      INTEGER            BOOLEAN                BOOLEAN
decimal   TEXT               NUMERIC(p, s)          DECIMAL(p, s)
datetime  TEXT (ISO, UTC)    TIMESTAMPTZ            DATETIME(6) (UTC)
date      TEXT               DATE                   DATE
time      TEXT               TIME                   TIME(6)
json      TEXT               JSONB                  JSON
bytes     BLOB               BYTEA                  LONGBLOB
uuid      TEXT               UUID                   CHAR(36)
========  =================  =====================  ==================
"""

from __future__ import annotations

import re

KINDS = ("int", "bigint", "str", "text", "float", "bool", "decimal", "datetime",
         "date", "time", "json", "bytes", "uuid")

# Families: what two column types must agree on to count as "the same" when
# comparing models with a live database (int vs bigint is not a change worth a migration).
_FAMILY = {"int": "int", "bigint": "int", "str": "text", "text": "text", "float": "float",
           "bool": "bool", "decimal": "decimal", "datetime": "datetime", "date": "date",
           "time": "time", "json": "json", "bytes": "bytes", "uuid": "uuid"}


class Dialect:
    name = "generic"
    quote_char = '"'
    supports_returning = False
    supports_skip_locked = False
    supports_transactional_ddl = True
    insert_ignore = "INSERT OR IGNORE INTO"
    insert_ignore_suffix = ""
    explain = "EXPLAIN"

    # -- identifiers -----------------------------------------------------
    def quote(self, name):
        q = self.quote_char
        return q + str(name).replace(q, q + q) + q

    def qualified(self, table, column):
        return f"{self.quote(table)}.{self.quote(column)}"

    # -- types -----------------------------------------------------------
    def type_sql(self, kind, *, max_length=None, precision=None, scale=None):
        raise NotImplementedError

    def pk_sql(self, kind="int"):
        """Column definition (after the name) for an auto-increment primary key."""
        raise NotImplementedError

    def fk_kind(self):
        """The kind a foreign key column takes to match an auto-increment primary key."""
        return "bigint"

    def family_of_kind(self, kind):
        return _FAMILY.get(kind, kind)

    def family_of_type(self, type_name):
        """The family of a column type as the database reports it."""
        raise NotImplementedError

    def kind_of_type(self, type_name):
        """Best-effort kind for a reported type (used when adopting an existing database)."""
        fam = self.family_of_type(type_name)
        base = (type_name or "").lower()
        if fam == "int":
            return "bigint" if "big" in base else "int"
        if fam == "text":
            return "str" if ("char" in base and "text" not in base) else "text"
        return fam

    def literal(self, value):
        """A *constant* default for DDL (only ever bool/int/float/str from model code)."""
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return self.bool_literal(value)
        if isinstance(value, (int, float)):
            return repr(value)
        if isinstance(value, str):
            return "'" + value.replace("'", "''") + "'"
        raise TypeError(f"{value!r} can't be a column default")

    def bool_literal(self, value):
        return "TRUE" if value else "FALSE"

    # -- statements ------------------------------------------------------
    def limit_offset(self, limit, offset):
        sql = ""
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        if offset:
            if limit is None:
                sql += " LIMIT -1"
            sql += f" OFFSET {int(offset)}"
        return sql

    def upsert_sql(self, table, cols, key_cols, update_cols):
        q = self.quote
        sql = (f"INSERT INTO {q(table)} ({', '.join(map(q, cols))}) "
               f"VALUES ({', '.join('?' for _ in cols)})")
        if update_cols:
            sets = ", ".join(f"{q(c)} = excluded.{q(c)}" for c in update_cols)
            return sql + f" ON CONFLICT ({', '.join(map(q, key_cols))}) DO UPDATE SET {sets}"
        return sql + f" ON CONFLICT ({', '.join(map(q, key_cols))}) DO NOTHING"

    def like(self, column_sql, ci):
        return (f"LOWER({column_sql}) LIKE LOWER(?) ESCAPE '\\'" if ci
                else f"{column_sql} LIKE ? ESCAPE '\\'")


class SQLiteDialect(Dialect):
    name = "sqlite"
    supports_returning = False        # lastrowid works everywhere; RETURNING needs SQLite 3.35+
    explain = "EXPLAIN QUERY PLAN"

    _TYPES = {"int": "INTEGER", "bigint": "INTEGER", "str": "TEXT", "text": "TEXT", "float": "REAL",
              "bool": "INTEGER", "decimal": "TEXT", "datetime": "TEXT", "date": "TEXT",
              "time": "TEXT", "json": "TEXT", "bytes": "BLOB", "uuid": "TEXT"}

    def type_sql(self, kind, *, max_length=None, precision=None, scale=None):
        return self._TYPES[kind]

    def pk_sql(self, kind="int"):
        return "INTEGER PRIMARY KEY AUTOINCREMENT"

    def fk_kind(self):
        return "int"

    def family_of_kind(self, kind):
        # SQLite stores by affinity: booleans are integers, dates/decimals/json are text.
        return {"int": "int", "bigint": "int", "bool": "int", "float": "float", "bytes": "bytes"}.get(kind, "text")

    def family_of_type(self, type_name):
        t = (type_name or "").upper()
        if "INT" in t or t in ("BOOLEAN", "BOOL"):
            return "int"
        if any(k in t for k in ("CHAR", "CLOB", "TEXT")) or t in ("DATETIME", "DATE", "TIME", "JSON", "UUID", "DECIMAL", "NUMERIC"):
            return "text"
        if t == "BLOB" or t == "":
            return "bytes"
        if any(k in t for k in ("REAL", "FLOA", "DOUB")):
            return "float"
        return "text"

    def kind_of_type(self, type_name):
        t = (type_name or "").upper()
        if t in ("BOOLEAN", "BOOL"):
            return "bool"
        return {"int": "int", "float": "float", "bytes": "bytes"}.get(self.family_of_type(type_name), "text")

    def bool_literal(self, value):
        return "1" if value else "0"


class PostgresDialect(Dialect):
    name = "postgres"
    supports_returning = True
    supports_skip_locked = True
    insert_ignore = "INSERT INTO"
    insert_ignore_suffix = " ON CONFLICT DO NOTHING"

    def type_sql(self, kind, *, max_length=None, precision=None, scale=None):
        if kind == "str":
            return f"VARCHAR({int(max_length)})" if max_length else "TEXT"
        if kind == "decimal":
            return f"NUMERIC({int(precision or 12)}, {int(scale if scale is not None else 2)})"
        return {"int": "INTEGER", "bigint": "BIGINT", "text": "TEXT", "float": "DOUBLE PRECISION",
                "bool": "BOOLEAN", "datetime": "TIMESTAMPTZ", "date": "DATE", "time": "TIME",
                "json": "JSONB", "bytes": "BYTEA", "uuid": "UUID"}[kind]

    def pk_sql(self, kind="int"):
        return "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"

    def family_of_type(self, type_name):
        t = (type_name or "").lower()
        if t in ("integer", "bigint", "smallint", "int", "int2", "int4", "int8", "serial", "bigserial"):
            return "int"
        if "char" in t or t == "text" or t == "citext":
            return "text"
        if t in ("double precision", "real", "float4", "float8"):
            return "float"
        if t in ("boolean", "bool"):
            return "bool"
        if t.startswith("numeric") or t == "decimal":
            return "decimal"
        if t.startswith("timestamp"):
            return "datetime"
        if t == "date":
            return "date"
        if t.startswith("time"):
            return "time"
        if t in ("json", "jsonb"):
            return "json"
        if t == "bytea":
            return "bytes"
        if t == "uuid":
            return "uuid"
        return t


class MySQLDialect(Dialect):
    name = "mysql"
    quote_char = "`"
    supports_skip_locked = True       # MySQL 8+
    supports_transactional_ddl = False
    insert_ignore = "INSERT IGNORE INTO"

    def type_sql(self, kind, *, max_length=None, precision=None, scale=None):
        if kind == "str":
            return f"VARCHAR({int(max_length or 255)})"
        if kind == "decimal":
            return f"DECIMAL({int(precision or 12)}, {int(scale if scale is not None else 2)})"
        return {"int": "INT", "bigint": "BIGINT", "text": "LONGTEXT", "float": "DOUBLE",
                "bool": "BOOLEAN", "datetime": "DATETIME(6)", "date": "DATE", "time": "TIME(6)",
                "json": "JSON", "bytes": "LONGBLOB", "uuid": "CHAR(36)"}[kind]

    def pk_sql(self, kind="int"):
        return "BIGINT AUTO_INCREMENT PRIMARY KEY"

    def family_of_kind(self, kind):
        # MySQL reports BOOLEAN as tinyint(1) and UUIDs as char(36).
        return {"bool": "bool", "uuid": "text"}.get(kind, _FAMILY.get(kind, kind))

    def family_of_type(self, type_name):
        t = (type_name or "").lower()
        if t.startswith("tinyint(1)") or t in ("boolean", "bool"):
            return "bool"
        if re.match(r"^(tiny|small|medium|big)?int", t):
            return "int"
        if "char" in t or "text" in t:
            return "text"
        if t.startswith(("double", "float", "real")):
            return "float"
        if t.startswith(("decimal", "numeric")):
            return "decimal"
        if t.startswith(("datetime", "timestamp")):
            return "datetime"
        if t == "date":
            return "date"
        if t.startswith("time"):
            return "time"
        if t == "json":
            return "json"
        if "blob" in t or "binary" in t:
            return "bytes"
        return t

    def limit_offset(self, limit, offset):
        sql = ""
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        if offset:
            if limit is None:
                sql += " LIMIT 18446744073709551615"
            sql += f" OFFSET {int(offset)}"
        return sql

    def upsert_sql(self, table, cols, key_cols, update_cols):
        q = self.quote
        sql = (f"INSERT INTO {q(table)} ({', '.join(map(q, cols))}) "
               f"VALUES ({', '.join('?' for _ in cols)})")
        targets = update_cols or [key_cols[0]]       # a no-op update stands in for DO NOTHING
        return sql + " ON DUPLICATE KEY UPDATE " + ", ".join(f"{q(c)} = VALUES({q(c)})" for c in targets)

    def like(self, column_sql, ci):
        # MySQL's default collations compare case-insensitively already.
        return (f"LOWER({column_sql}) LIKE LOWER(?) ESCAPE '\\\\'" if ci
                else f"{column_sql} LIKE ? ESCAPE '\\\\'")


SQLITE = SQLiteDialect()
POSTGRES = PostgresDialect()
MYSQL = MySQLDialect()


def dialect_of(db):
    """The dialect for a database object (SQLite for anything unknown, e.g. test doubles)."""
    found = getattr(db, "dialect", None)
    if isinstance(found, Dialect):
        return found
    name = type(db).__name__.lower()
    if "postgres" in name:
        return POSTGRES
    if "mysql" in name:
        return MYSQL
    return SQLITE
