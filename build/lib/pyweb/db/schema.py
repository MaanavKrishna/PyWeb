"""Schemas: what the Models describe, what the database has, and the steps between.

* :func:`from_models` turns Model classes into :class:`Table` specs.
* :func:`introspect` reads the same specs from a live SQLite, Postgres or
  MySQL database.
* :func:`diff` compares the two and returns migration operations. Safe
  steps (*expand*: new tables, new nullable columns, new indexes) and
  destructive ones (*contract*: dropping or narrowing) are kept apart, so a
  deploy never breaks the code still running from the last release.
* :class:`Operations` runs operations against a database (the ``op`` in
  migration files), rebuilding SQLite tables where SQLite can't ``ALTER``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json

from .dialect import dialect_of

INTERNAL_PREFIXES = ("pyweb_", "sqlite_", "_pyweb_")
_UNSET = object()


@dataclasses.dataclass
class Column:
    name: str
    kind: str
    nullable: bool = True
    default: object = None
    primary_key: bool = False
    autoincrement: bool = False
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None
    references: tuple | None = None          # (table, column, on_delete)

    def to_code(self):
        args = [repr(self.name), repr(self.kind)]
        for key, default in (("nullable", True), ("default", None), ("primary_key", False), ("autoincrement", False),
                             ("max_length", None), ("precision", None), ("scale", None), ("references", None)):
            val = getattr(self, key)
            if val != default:
                args.append(f"{key}={val!r}")
        return f"op.column({', '.join(args)})"

    def canonical(self):
        return {"kind": self.kind, "nullable": self.nullable, "pk": self.primary_key,
                "max": self.max_length, "ref": list(self.references[:2]) if self.references else None}


@dataclasses.dataclass
class Index:
    name: str
    columns: tuple
    unique: bool = False

    def to_code(self):
        return f"op.index({self.name!r}, {list(self.columns)!r}{', unique=True' if self.unique else ''})"


@dataclasses.dataclass
class Table:
    name: str
    columns: list
    indexes: list = dataclasses.field(default_factory=list)

    def column(self, name):
        for c in self.columns:
            if c.name == name:
                return c
        return None

    def to_code(self):
        cols = ",\n    ".join(c.to_code() for c in self.columns)
        out = f"op.create_table({self.name!r}, [\n    {cols},\n]"
        if self.indexes:
            idx = ", ".join(i.to_code() for i in self.indexes)
            out += f", indexes=[{idx}]"
        return out + ")"

    def depends_on(self):
        return {c.references[0] for c in self.columns if c.references and c.references[0] != self.name}


def index_name(prefix, table, columns):
    name = f"{prefix}_{table}_{'_'.join(columns)}"
    if len(name) > 60:
        name = name[:50] + "_" + hashlib.sha1(name.encode(), usedforsecurity=False).hexdigest()[:8]
    return name


# -------------------------------------------------------------- from models

def from_models(models):
    """``{table: Table}`` for Model classes (with their many-to-many join tables)."""
    tables = {}
    for model in models:
        meta = model._meta
        if meta.abstract:
            continue
        cols, indexes = [], []
        for f in meta.columns():
            ref = None
            rel = getattr(f, "relation", None)
            if rel is not None:
                target = rel.target._meta
                ref = (target.table, target.pk.column, rel.on_delete)
            auto = f.primary_key and f.kind in ("int", "bigint")
            cols.append(Column(f.column, "bigint" if auto else f.kind, nullable=f.nullable,
                               default=None if f.primary_key else f.db_default(), primary_key=f.primary_key,
                               autoincrement=auto, max_length=f.max if (f.kind == "str" and isinstance(f.max, int)) else None,
                               precision=f.precision, scale=f.scale, references=ref))
            if f.unique and not f.primary_key:
                indexes.append(Index(index_name("uq", meta.table, [f.column]), (f.column,), True))
            elif f.index and not f.primary_key:
                indexes.append(Index(index_name("ix", meta.table, [f.column]), (f.column,), False))
        for idx in meta.indexes:
            colnames = tuple(meta.field(n).column for n in idx.fields)
            indexes.append(Index(idx.name or index_name("uq" if idx.unique else "ix", meta.table, colnames),
                                 colnames, idx.unique))
        tables[meta.table] = Table(meta.table, cols, indexes)
        for m2m in meta.m2m.values():
            jt = m2m.table
            if jt in tables:
                continue
            left_ref = (meta.table, meta.pk.column, "cascade")
            right_ref = (m2m.target._meta.table, m2m.target._meta.pk.column, "cascade")
            tables[jt] = Table(jt, [Column(m2m.left, "bigint", nullable=False, references=left_ref),
                                    Column(m2m.right, "bigint", nullable=False, references=right_ref)],
                               [Index(index_name("uq", jt, [m2m.left, m2m.right]), (m2m.left, m2m.right), True),
                                Index(index_name("ix", jt, [m2m.right]), (m2m.right,), False)])
    return tables


def schema_hash(tables):
    data = {name: {"columns": {c.name: c.canonical() for c in t.columns},
                   "indexes": sorted([list(i.columns), i.unique] for i in t.indexes)}
            for name, t in tables.items()}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def ordered(tables):
    """Tables with every foreign key target before the tables that point at it."""
    pending = dict(tables)
    out, done = [], set()
    while pending:
        progressed = False
        for name in sorted(pending):
            t = pending[name]
            if all(dep in done or dep not in tables for dep in t.depends_on()):
                out.append(t)
                done.add(name)
                del pending[name]
                progressed = True
        if not progressed:                       # a cycle: create the rest in name order
            for name in sorted(pending):
                out.append(pending[name])
            break
    return out


# --------------------------------------------------------------------- DDL

def _column_sql(col, d, *, inline_ref=False):
    q = d.quote
    if col.primary_key and col.autoincrement:
        return f"{q(col.name)} {d.pk_sql(col.kind)}"
    parts = [q(col.name), d.type_sql(col.kind, max_length=col.max_length, precision=col.precision, scale=col.scale)]
    if col.primary_key:
        parts.append("NOT NULL PRIMARY KEY")
    elif not col.nullable:
        parts.append("NOT NULL")
    if col.default is not None and not (d.name == "mysql" and col.kind in ("text", "json", "bytes")):
        parts.append("DEFAULT " + d.literal(col.default))
    if inline_ref and col.references:
        table, column, on_delete = col.references
        parts.append(f"REFERENCES {q(table)} ({q(column)}) ON DELETE {_on_delete(on_delete)}")
    return " ".join(parts)


def _on_delete(value):
    from pyweb.models.relations import ON_DELETE
    return ON_DELETE.get(str(value).lower(), "CASCADE")


def fk_name(table, column):
    return index_name("fk", table, [column])


def create_table_sql(table, d, *, constraint_table=None):
    q = d.quote
    lines = [_column_sql(c, d) for c in table.columns]
    for c in table.columns:
        if c.references:
            t, col, on_delete = c.references
            lines.append(f"CONSTRAINT {q(fk_name(constraint_table or table.name, c.name))} FOREIGN KEY ({q(c.name)}) "
                         f"REFERENCES {q(t)} ({q(col)}) ON DELETE {_on_delete(on_delete)}")
    stmts = [f"CREATE TABLE {q(table.name)} (\n  " + ",\n  ".join(lines) + "\n)"]
    stmts += [create_index_sql(table.name, i, d) for i in table.indexes]
    return stmts


def create_index_sql(table, index, d):
    q = d.quote
    return (f"CREATE {'UNIQUE ' if index.unique else ''}INDEX {q(index.name)} ON {q(table)} "
            f"({', '.join(q(c) for c in index.columns)})")


def drop_index_sql(table, name, d):
    if d.name == "mysql":
        return f"DROP INDEX {d.quote(name)} ON {d.quote(table)}"
    return f"DROP INDEX {d.quote(name)}"


# --------------------------------------------------------------- introspect

def _rows(db, sql, params=()):
    return [tuple(r) for r in db.execute(sql, params).fetchall()]


def _parse_default(raw):
    if raw is None:
        return None
    text = str(raw).strip()
    if text.upper() == "NULL":
        return None
    while text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    if "::" in text:                                 # Postgres: 'x'::character varying
        text = text.split("::", 1)[0]
    if text.startswith("'") and text.endswith("'") and len(text) >= 2:
        return text[1:-1].replace("''", "'")
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return None                                  # an expression (now(), nextval(...)): not a constant


def introspect(db, *, include_internal=False):
    """``{table: Table}`` as the database has them now."""
    d = dialect_of(db)
    fn = {"sqlite": _introspect_sqlite, "postgres": _introspect_postgres, "mysql": _introspect_mysql}[d.name]
    tables = fn(db, d)
    if not include_internal:
        tables = {k: v for k, v in tables.items() if not k.startswith(INTERNAL_PREFIXES)}
    return tables


def table_names(db):
    d = dialect_of(db)
    if d.name == "sqlite":
        rows = _rows(db, "SELECT name FROM sqlite_master WHERE type = 'table'")
    elif d.name == "postgres":
        rows = _rows(db, "SELECT table_name FROM information_schema.tables "
                         "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'")
    else:
        rows = _rows(db, "SELECT table_name FROM information_schema.tables "
                         "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'")
    return {r[0] for r in rows}


def _introspect_sqlite(db, d):
    tables = {}
    for (name,) in _rows(db, "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"):
        q = d.quote(name)
        cols = []
        info = _rows(db, f"PRAGMA table_info({q})")
        pks = [r for r in info if r[5]]
        for cid, cname, ctype, notnull, dflt, pk in info:
            auto = bool(pk) and len(pks) == 1 and "INT" in (ctype or "").upper()
            kind = d.kind_of_type(ctype)
            cols.append(Column(cname, "bigint" if auto else kind, nullable=not notnull and not pk,
                               default=None if pk else _parse_default(dflt), primary_key=bool(pk),
                               autoincrement=auto))
        for row in _rows(db, f"PRAGMA foreign_key_list({q})"):
            col = next((c for c in cols if c.name == row[3]), None)
            if col is not None:
                col.references = (row[2], row[4] or "id", (row[6] or "NO ACTION").lower())
        indexes = []
        for row in _rows(db, f"PRAGMA index_list({q})"):
            iname, unique, origin = row[1], row[2], row[3] if len(row) > 3 else "c"
            if origin == "pk":
                continue
            colnames = tuple(r[2] for r in _rows(db, f"PRAGMA index_info({d.quote(iname)})"))
            indexes.append(Index(iname, colnames, bool(unique)))
        tables[name] = Table(name, cols, indexes)
    return tables


_PG_DELETE = {"a": "no action", "r": "restrict", "c": "cascade", "n": "set null", "d": "set default"}


def _introspect_postgres(db, d):
    tables = {}
    rows = _rows(db, """
        SELECT c.table_name, c.column_name, c.data_type, c.is_nullable, c.column_default,
               c.character_maximum_length, c.numeric_precision, c.numeric_scale, c.is_identity
        FROM information_schema.columns c
        JOIN information_schema.tables t ON t.table_name = c.table_name AND t.table_schema = c.table_schema
        WHERE c.table_schema = current_schema() AND t.table_type = 'BASE TABLE'
        ORDER BY c.table_name, c.ordinal_position""")
    pks = {(r[0], r[1]) for r in _rows(db, """
        SELECT tc.table_name, kcu.column_name FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = current_schema()""")}
    for tname, cname, dtype, nullable, default, maxlen, prec, scale, identity in rows:
        is_pk = (tname, cname) in pks
        auto = is_pk and (identity == "YES" or "nextval(" in str(default or ""))
        kind = d.kind_of_type(dtype)
        if kind == "text" and maxlen:
            kind = "str"
        col = Column(cname, "bigint" if auto else kind, nullable=nullable == "YES" and not is_pk,
                     default=None if auto else _parse_default(default), primary_key=is_pk, autoincrement=auto,
                     max_length=maxlen if kind == "str" else None,
                     precision=prec if kind == "decimal" else None, scale=scale if kind == "decimal" else None)
        tables.setdefault(tname, Table(tname, [], [])).columns.append(col)
    for tname, cname, ftable, fcol, deltype in _rows(db, """
        SELECT cl.relname, att.attname, fcl.relname, fatt.attname, con.confdeltype
        FROM pg_constraint con
        JOIN pg_class cl ON cl.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = cl.relnamespace
        JOIN pg_class fcl ON fcl.oid = con.confrelid
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = con.conkey[1]
        JOIN pg_attribute fatt ON fatt.attrelid = con.confrelid AND fatt.attnum = con.confkey[1]
        WHERE con.contype = 'f' AND n.nspname = current_schema()"""):
        t = tables.get(tname)
        col = t.column(cname) if t else None
        if col is not None:
            col.references = (ftable, fcol, _PG_DELETE.get(str(deltype), "no action"))
    for tname, iname, unique, cols in _rows(db, """
        SELECT t.relname, i.relname, ix.indisunique, array_agg(a.attname ORDER BY k.ord)
        FROM pg_index ix
        JOIN pg_class t ON t.oid = ix.indrelid
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
        WHERE n.nspname = current_schema() AND NOT ix.indisprimary
        GROUP BY t.relname, i.relname, ix.indisunique"""):
        if tname in tables:
            if isinstance(cols, str):                        # drivers without array support
                cols = [c.strip() for c in cols.strip("{}").split(",") if c.strip()]
            tables[tname].indexes.append(Index(iname, tuple(cols), bool(unique)))
    return tables


def _introspect_mysql(db, d):
    tables = {}
    for tname, cname, ctype, nullable, default, key, extra, maxlen, prec, scale in _rows(db, """
        SELECT table_name, column_name, column_type, is_nullable, column_default, column_key, extra,
               character_maximum_length, numeric_precision, numeric_scale
        FROM information_schema.columns WHERE table_schema = DATABASE()
        ORDER BY table_name, ordinal_position"""):
        if isinstance(ctype, bytes):
            ctype = ctype.decode()
        is_pk = key == "PRI"
        auto = is_pk and "auto_increment" in str(extra or "").lower()
        kind = d.kind_of_type(ctype)
        lowered = ctype.lower()
        if lowered.startswith("varchar") or lowered.startswith("char("):
            kind = "str" if lowered != "char(36)" else "uuid"
        col = Column(cname, "bigint" if auto else kind, nullable=nullable == "YES" and not is_pk,
                     default=None if auto else _parse_default(default if default is None or isinstance(default, str)
                                                              else repr(default)),
                     primary_key=is_pk, autoincrement=auto, max_length=maxlen if kind == "str" else None,
                     precision=prec if kind == "decimal" else None, scale=scale if kind == "decimal" else None)
        tables.setdefault(tname, Table(tname, [], [])).columns.append(col)
    for tname, cname, ftable, fcol, rule in _rows(db, """
        SELECT kcu.table_name, kcu.column_name, kcu.referenced_table_name, kcu.referenced_column_name, rc.delete_rule
        FROM information_schema.key_column_usage kcu
        JOIN information_schema.referential_constraints rc
          ON rc.constraint_name = kcu.constraint_name AND rc.constraint_schema = kcu.table_schema
        WHERE kcu.table_schema = DATABASE() AND kcu.referenced_table_name IS NOT NULL"""):
        t = tables.get(tname)
        col = t.column(cname) if t else None
        if col is not None:
            col.references = (ftable, fcol, str(rule).lower())
    grouped: dict = {}
    for tname, iname, non_unique, cname in _rows(db, """
        SELECT table_name, index_name, non_unique, column_name FROM information_schema.statistics
        WHERE table_schema = DATABASE() ORDER BY table_name, index_name, seq_in_index"""):
        if iname == "PRIMARY":
            continue
        grouped.setdefault((tname, iname), [not int(non_unique), []])[1].append(cname)
    for (tname, iname), (unique, cols) in grouped.items():
        if tname in tables:
            tables[tname].indexes.append(Index(iname, tuple(cols), unique))
    return tables


# ------------------------------------------------------------- operations

class Op:
    destructive = False

    def run(self, op):
        raise NotImplementedError

    def reverse(self):
        raise NotImplementedError

    def to_code(self):
        raise NotImplementedError

    def describe(self):
        return self.to_code()


class CreateTable(Op):
    def __init__(self, table):
        self.table = table

    def run(self, op):
        op.create_table(self.table.name, self.table.columns, indexes=self.table.indexes)

    def reverse(self):
        return DropTable(self.table)

    def to_code(self):
        return self.table.to_code()

    def describe(self):
        return f"create table {self.table.name}"


class DropTable(Op):
    destructive = True

    def __init__(self, table):
        self.table = table

    def run(self, op):
        op.drop_table(self.table.name)

    def reverse(self):
        return CreateTable(self.table)

    def to_code(self):
        return f"op.drop_table({self.table.name!r})"

    def describe(self):
        return f"drop table {self.table.name} (its data is lost)"


class AddColumn(Op):
    def __init__(self, table, column):
        self.table, self.column = table, column

    def run(self, op):
        op.add_column(self.table, self.column)

    def reverse(self):
        return DropColumn(self.table, self.column)

    def to_code(self):
        return f"op.add_column({self.table!r}, {self.column.to_code()})"

    def describe(self):
        return f"add column {self.table}.{self.column.name}"


class DropColumn(Op):
    destructive = True

    def __init__(self, table, column):
        self.table, self.column = table, column

    def run(self, op):
        op.drop_column(self.table, self.column.name)

    def reverse(self):
        return AddColumn(self.table, self.column)

    def to_code(self):
        return f"op.drop_column({self.table!r}, {self.column.name!r})"

    def describe(self):
        return f"drop column {self.table}.{self.column.name} (its data is lost)"


class AlterColumn(Op):
    def __init__(self, table, old, new):
        self.table, self.old, self.new = table, old, new
        narrowing = (old.nullable and not new.nullable)
        self.destructive = narrowing or old.kind != new.kind or (
            old.max_length and new.max_length and new.max_length < old.max_length)

    def run(self, op):
        op.alter_column(self.table, self.new.name, kind=self.new.kind, nullable=self.new.nullable,
                        default=self.new.default, max_length=self.new.max_length,
                        precision=self.new.precision, scale=self.new.scale)

    def reverse(self):
        return AlterColumn(self.table, self.new, self.old)

    def to_code(self):
        n = self.new
        args = [repr(self.table), repr(n.name), f"kind={n.kind!r}", f"nullable={n.nullable!r}"]
        if n.default is not None:
            args.append(f"default={n.default!r}")
        if n.max_length:
            args.append(f"max_length={n.max_length!r}")
        if n.precision is not None:
            args.append(f"precision={n.precision!r}, scale={n.scale!r}")
        return f"op.alter_column({', '.join(args)})"

    def describe(self):
        changes = []
        if self.old.kind != self.new.kind:
            changes.append(f"type {self.old.kind} -> {self.new.kind}")
        if self.old.nullable != self.new.nullable:
            changes.append("allow empty" if self.new.nullable else "require a value")
        if self.old.max_length != self.new.max_length:
            changes.append(f"max length {self.old.max_length} -> {self.new.max_length}")
        return f"change {self.table}.{self.new.name}: {', '.join(changes) or 'definition'}"


class RenameColumn(Op):
    def __init__(self, table, old, new):
        self.table, self.old, self.new = table, old, new

    def run(self, op):
        op.rename_column(self.table, self.old, self.new)

    def reverse(self):
        return RenameColumn(self.table, self.new, self.old)

    def to_code(self):
        return f"op.rename_column({self.table!r}, {self.old!r}, {self.new!r})"

    def describe(self):
        return f"rename {self.table}.{self.old} to {self.new}"


class RenameTable(Op):
    def __init__(self, old, new):
        self.old, self.new = old, new

    def run(self, op):
        op.rename_table(self.old, self.new)

    def reverse(self):
        return RenameTable(self.new, self.old)

    def to_code(self):
        return f"op.rename_table({self.old!r}, {self.new!r})"

    def describe(self):
        return f"rename table {self.old} to {self.new}"


class CreateIndex(Op):
    def __init__(self, table, index):
        self.table, self.index = table, index

    def run(self, op):
        op.create_index(self.table, self.index.name, list(self.index.columns), unique=self.index.unique)

    def reverse(self):
        return DropIndex(self.table, self.index)

    def to_code(self):
        return (f"op.create_index({self.table!r}, {self.index.name!r}, {list(self.index.columns)!r}"
                f"{', unique=True' if self.index.unique else ''})")

    def describe(self):
        return f"add {'unique ' if self.index.unique else ''}index on {self.table} ({', '.join(self.index.columns)})"


class DropIndex(Op):
    def __init__(self, table, index):
        self.table, self.index = table, index

    def run(self, op):
        op.drop_index(self.table, self.index.name)

    def reverse(self):
        return CreateIndex(self.table, self.index)

    def to_code(self):
        return f"op.drop_index({self.table!r}, {self.index.name!r})"

    def describe(self):
        return f"drop index {self.index.name}"


class Execute(Op):
    def __init__(self, sql, params=(), reverse_sql=None, note=None):
        self.sql, self.params, self.reverse_sql, self.note = sql, tuple(params), reverse_sql, note

    def run(self, op):
        op.execute(self.sql, self.params)

    def reverse(self):
        return Execute(self.reverse_sql) if self.reverse_sql else None

    def to_code(self):
        code = f"op.execute({self.sql!r}{', ' + repr(self.params) if self.params else ''})"
        return (f"# {self.note}\n" + code) if self.note else code

    def describe(self):
        return self.note or self.sql


# -------------------------------------------------------------------- diff

class Plan:
    """The result of :func:`diff`: ``expand`` steps are safe while old code still
    runs; ``contract`` steps remove things and run after every server is on the new code."""

    def __init__(self, expand=None, contract=None, notes=None):
        self.expand = list(expand or [])
        self.contract = list(contract or [])
        self.notes = list(notes or [])

    def __bool__(self):
        return bool(self.expand or self.contract)

    @property
    def ops(self):
        return self.expand + self.contract


def _same_column(old, new, d):
    if old.primary_key and new.primary_key:
        return True
    if d.family_of_type(d.type_sql(old.kind, max_length=old.max_length)) != \
            d.family_of_type(d.type_sql(new.kind, max_length=new.max_length)):
        return False
    if old.nullable != new.nullable:
        return False
    if d.name != "sqlite" and old.kind == "str" and new.kind == "str" and (old.max_length or 0) != (new.max_length or 0) \
            and old.max_length and new.max_length:
        return False
    return True


def diff(current, target, dialect, *, renames=None, table_renames=None):
    """Steps that turn the ``current`` schema into ``target`` (both ``{table: Table}``).

    ``renames`` (``{"posts.title": "headline"}``) and ``table_renames``
    (``{"posts": "articles"}``) say which differences are renames rather than
    a drop plus an add.
    """
    d = dialect
    renames = dict(renames or {})
    table_renames = dict(table_renames or {})
    plan = Plan()
    current = dict(current)
    for old_name, new_name in table_renames.items():
        if old_name in current and new_name in target and new_name not in current:
            plan.expand.append(RenameTable(old_name, new_name))
            t = current.pop(old_name)
            current[new_name] = Table(new_name, t.columns, t.indexes)
    creating = {n: t for n, t in target.items() if n not in current}
    for t in ordered(creating):
        plan.expand.append(CreateTable(t))
    for name in sorted(set(current) - set(target)):
        plan.notes.append(f"table {name} is in the database but no Model uses it (kept; drop it in a migration "
                          f"with op.drop_table({name!r}) if you mean to)")
    for name in sorted(set(current) & set(target)):
        old, new = current[name], target[name]
        old_cols = {c.name: c for c in old.columns}
        new_cols = {c.name: c for c in new.columns}
        renamed_from = {}
        for key, new_col in renames.items():
            tname, _, col = key.partition(".")
            if tname == name and col in old_cols and new_col in new_cols and new_col not in old_cols:
                renamed_from[new_col] = col
        for cname, col in new_cols.items():
            if cname in old_cols:
                if not _same_column(old_cols[cname], col, d):
                    alter = AlterColumn(name, old_cols[cname], col)
                    (plan.contract if alter.destructive else plan.expand).append(alter)
                continue
            if cname in renamed_from:
                src = renamed_from[cname]
                # expand: add the new column (empty allowed) and copy; contract: require it and drop the old one
                staged = dataclasses.replace(col, nullable=True)
                plan.expand.append(AddColumn(name, staged))
                plan.expand.append(Execute(f"UPDATE {d.quote(name)} SET {d.quote(cname)} = {d.quote(src)}",
                                           note=f"copy {name}.{src} into {cname}; keep writing both until the contract step"))
                if not col.nullable:
                    plan.contract.append(AlterColumn(name, staged, col))
                plan.contract.append(DropColumn(name, old_cols[src]))
                continue
            if not col.nullable and col.default is None and not col.primary_key:
                staged = dataclasses.replace(col, nullable=True)
                plan.expand.append(AddColumn(name, staged))
                plan.notes.append(f"{name}.{cname} is required but existing rows have no value: it is added as "
                                  f"optional now; fill it in, then the contract step makes it required")
                plan.contract.append(AlterColumn(name, staged, col))
            else:
                plan.expand.append(AddColumn(name, col))
        for cname, col in old_cols.items():
            if cname not in new_cols and cname not in renamed_from.values():
                plan.contract.append(DropColumn(name, col))
        have = {(tuple(i.columns), i.unique) for i in old.indexes}
        want = {(tuple(i.columns), i.unique) for i in new.indexes}
        for idx in new.indexes:
            if (tuple(idx.columns), idx.unique) not in have and all(c in new_cols for c in idx.columns):
                plan.expand.append(CreateIndex(name, idx))
        for idx in old.indexes:
            if (tuple(idx.columns), idx.unique) not in want and idx.name.startswith(("ix_", "uq_")) \
                    and all(c in new_cols for c in idx.columns):
                plan.contract.append(DropIndex(name, idx))
    return plan


# ------------------------------------------------------------- operations

class Operations:
    """The ``op`` object migrations receive. Each method runs its SQL now."""

    def __init__(self, db):
        self.db = db
        self.dialect = dialect_of(db)

    # spec helpers used by generated code
    @staticmethod
    def column(name, kind, **kw):
        return Column(name, kind, **kw)

    @staticmethod
    def index(name, columns, unique=False):
        return Index(name, tuple(columns), unique)

    def execute(self, sql, params=()):
        return self.db.execute(sql, params)

    def run_python(self, fn):
        return fn(self.db)

    def _live(self, table):
        found = introspect(self.db, include_internal=True).get(table)
        if found is None:
            raise ValueError(f"table {table!r} doesn't exist")
        return found

    def create_table(self, name, columns, *, indexes=()):
        for stmt in create_table_sql(Table(name, list(columns), list(indexes)), self.dialect):
            self.db.execute(stmt)

    def drop_table(self, name):
        self.db.execute(f"DROP TABLE {self.dialect.quote(name)}")

    def rename_table(self, old, new):
        q = self.dialect.quote
        self.db.execute(f"ALTER TABLE {q(old)} RENAME TO {q(new)}")

    def add_column(self, table, column):
        d, q = self.dialect, self.dialect.quote
        if not column.nullable and column.default is None and not column.primary_key:
            # A required column with no default can't be added to rows that exist (they'd be
            # empty): add it as optional, then require it if no row is left without a value.
            self.add_column(table, dataclasses.replace(column, nullable=True))
            empty = self.db.execute(f"SELECT COUNT(*) FROM {q(table)} WHERE {q(column.name)} IS NULL").fetchone()[0]
            if not empty:
                self.alter_column(table, column.name, nullable=False)
            else:
                import logging
                logging.getLogger("pyweb.db").warning(
                    "%s.%s was added as optional: %d existing row(s) have no value for it", table, column.name, empty)
            return
        if d.name == "sqlite":
            self.db.execute(f"ALTER TABLE {q(table)} ADD COLUMN {_column_sql(column, d, inline_ref=True)}")
            return
        self.db.execute(f"ALTER TABLE {q(table)} ADD COLUMN {_column_sql(column, d)}")
        if column.references:
            t, col, on_delete = column.references
            self.db.execute(f"ALTER TABLE {q(table)} ADD CONSTRAINT {q(fk_name(table, column.name))} FOREIGN KEY "
                            f"({q(column.name)}) REFERENCES {q(t)} ({q(col)}) ON DELETE {_on_delete(on_delete)}")

    def drop_column(self, table, name):
        d, q = self.dialect, self.dialect.quote
        if d.name == "sqlite":
            self._rebuild(table, drop=name)
            return
        live = self._live(table)
        for idx in live.indexes:
            if name in idx.columns and d.name == "mysql":
                pass                                   # MySQL drops the column's indexes with it
        col = live.column(name)
        if d.name == "mysql" and col is not None and col.references:
            for cname in _mysql_fk_names(self.db, table, name):
                self.db.execute(f"ALTER TABLE {q(table)} DROP FOREIGN KEY {q(cname)}")
        self.db.execute(f"ALTER TABLE {q(table)} DROP COLUMN {q(name)}")

    def rename_column(self, table, old, new):
        q = self.dialect.quote
        self.db.execute(f"ALTER TABLE {q(table)} RENAME COLUMN {q(old)} TO {q(new)}")

    def alter_column(self, table, name, *, kind=None, nullable=None, default=_UNSET, max_length=_UNSET,
                     precision=_UNSET, scale=_UNSET):
        d, q = self.dialect, self.dialect.quote
        live = self._live(table)
        col = live.column(name)
        if col is None:
            raise ValueError(f"{table}.{name} doesn't exist")
        new = dataclasses.replace(col)
        if kind is not None:
            new.kind = kind
        if nullable is not None:
            new.nullable = nullable
        if default is not _UNSET:
            new.default = default
        if max_length is not _UNSET:
            new.max_length = max_length
        if precision is not _UNSET:
            new.precision = precision
        if scale is not _UNSET:
            new.scale = scale
        if d.name == "sqlite":
            self._rebuild(table, replace=new)
        elif d.name == "postgres":
            tname = q(table)
            cname = q(name)
            if new.kind != col.kind or new.max_length != col.max_length or new.precision != col.precision:
                typ = d.type_sql(new.kind, max_length=new.max_length, precision=new.precision, scale=new.scale)
                self.db.execute(f"ALTER TABLE {tname} ALTER COLUMN {cname} TYPE {typ} USING {cname}::{typ}")
            if new.nullable != col.nullable:
                self.db.execute(f"ALTER TABLE {tname} ALTER COLUMN {cname} {'DROP' if new.nullable else 'SET'} NOT NULL")
            if new.default != col.default:
                if new.default is None:
                    self.db.execute(f"ALTER TABLE {tname} ALTER COLUMN {cname} DROP DEFAULT")
                else:
                    self.db.execute(f"ALTER TABLE {tname} ALTER COLUMN {cname} SET DEFAULT {d.literal(new.default)}")
        else:
            self.db.execute(f"ALTER TABLE {q(table)} MODIFY COLUMN {_column_sql(new, d)}")

    def create_index(self, table, name, columns, *, unique=False):
        self.db.execute(create_index_sql(table, Index(name, tuple(columns), unique), self.dialect))

    def drop_index(self, table, name):
        self.db.execute(drop_index_sql(table, name, self.dialect))

    def _rebuild(self, table, *, drop=None, replace=None):
        """SQLite's documented way to change a table: copy it into a new one."""
        q = self.dialect.quote
        live = self._live(table)
        cols = []
        for c in live.columns:
            if c.name == drop:
                continue
            cols.append(replace if (replace is not None and c.name == replace.name) else c)
        keep = [c.name for c in cols]
        temp = f"_pyweb_new_{table}"
        new = Table(temp, cols, [])
        for stmt in create_table_sql(new, self.dialect, constraint_table=table):
            self.db.execute(stmt)
        cols_sql = ", ".join(q(c) for c in keep)
        self.db.execute(f"INSERT INTO {q(temp)} ({cols_sql}) SELECT {cols_sql} FROM {q(table)}")
        self.db.execute(f"DROP TABLE {q(table)}")
        self.db.execute(f"ALTER TABLE {q(temp)} RENAME TO {q(table)}")
        for idx in live.indexes:
            if all(c in keep for c in idx.columns) and not idx.name.startswith("sqlite_autoindex"):
                self.db.execute(create_index_sql(table, idx, self.dialect))


def _mysql_fk_names(db, table, column):
    rows = _rows(db, "SELECT constraint_name FROM information_schema.key_column_usage WHERE table_schema = DATABASE() "
                     "AND table_name = ? AND column_name = ? AND referenced_table_name IS NOT NULL", (table, column))
    return [r[0] for r in rows]


def create_missing(db, tables):
    """Create the tables in ``tables`` that the database doesn't have yet (never alters)."""
    have = table_names(db)
    d = dialect_of(db)
    made = []
    for t in ordered({n: t for n, t in tables.items() if n not in have}):
        for stmt in create_table_sql(t, d):
            db.execute(stmt)
        made.append(t.name)
    return made
