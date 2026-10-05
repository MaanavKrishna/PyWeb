"""Queries: ``Post.where(Post.views > 10).order("-published_at").include("author")``.

A :class:`QuerySet` is immutable: every method returns a new one, so a base
query can be shared and refined safely. Nothing runs until the results are
used (iterated, indexed, ``len()``, ``first()``, ``count()``...).

Every value becomes a bound parameter and every column name comes from the
Model, so no input can change the shape of the SQL.
"""

from __future__ import annotations

import base64
import json

from pyweb.db.dialect import dialect_of

from .fields import Field

IN_CHUNK = 500      # stay far below SQLite's variable limit


class _Compiler:
    def __init__(self, dialect):
        self.d = dialect
        self.params = []

    def col(self, field):
        return self.d.qualified(field.model._meta.table, field.column)

    def value(self, field, value):
        from . import Model
        if isinstance(value, Model):
            value = value.pk
        if field is not None and value is not None:
            try:
                value = field.to_db(value, self.d)
            except (TypeError, ValueError, AttributeError):
                pass
        self.params.append(value)
        return "?"


# ------------------------------------------------------------------ conditions

class Cond:
    """A WHERE condition. Combine with ``&`` (and), ``|`` (or) and ``~`` (not)."""

    def __and__(self, other):
        return And([self, _as_cond(other)])

    def __or__(self, other):
        return Or([self, _as_cond(other)])

    def __invert__(self):
        return Not(self)

    def __bool__(self):
        raise TypeError("a query condition has no truth value: combine conditions with & and | "
                        "(not `and`/`or`), and pass them to .where()")

    def sql(self, c):
        raise NotImplementedError


def _as_cond(value):
    if isinstance(value, Cond):
        return value
    if value is True:
        return Raw("1 = 1")
    if value is False:
        return Raw("1 = 0")
    raise TypeError(f"expected a condition, got {value!r}")


class Raw(Cond):
    def __init__(self, text):
        self.text = text

    def sql(self, c):
        return self.text


class Compare(Cond):
    def __init__(self, field, op, value):
        self.field, self.op, self.value = field, op, value

    def __bool__(self):
        # `field_a == field_b` must still work in `in` checks and dict lookups.
        if self.op in ("=", "!=") and isinstance(self.value, Field):
            return (self.field is self.value) == (self.op == "=")
        return Cond.__bool__(self)

    def sql(self, c):
        left = c.col(self.field)
        if isinstance(self.value, Field):
            return f"{left} {self.op} {c.col(self.value)}"
        if self.value is None:
            if self.op == "=":
                return f"{left} IS NULL"
            if self.op == "!=":
                return f"{left} IS NOT NULL"
            raise ValueError(f"can't compare {self.field.name} {self.op} None")
        return f"{left} {self.op} {c.value(self.field, self.value)}"


class In(Cond):
    def __init__(self, field, values):
        self.field, self.values = field, values

    def sql(self, c):
        if not self.values:
            return "1 = 0"
        marks = ", ".join(c.value(self.field, v) for v in self.values)
        return f"{c.col(self.field)} IN ({marks})"


class Like(Cond):
    def __init__(self, field, pattern, *, ci, raw=False):
        self.field, self.pattern, self.ci = field, pattern, ci

    def sql(self, c):
        c.params.append(str(self.pattern))
        return c.d.like(c.col(self.field), self.ci)


class And(Cond):
    def __init__(self, parts):
        self.parts = [p for part in parts for p in (part.parts if isinstance(part, And) else [part])]

    def sql(self, c):
        return "(" + " AND ".join(p.sql(c) for p in self.parts) + ")"


class Or(Cond):
    def __init__(self, parts):
        self.parts = [p for part in parts for p in (part.parts if isinstance(part, Or) else [part])]

    def sql(self, c):
        return "(" + " OR ".join(p.sql(c) for p in self.parts) + ")"


class Not(Cond):
    def __init__(self, part):
        self.part = part

    def sql(self, c):
        return f"NOT ({self.part.sql(c)})"


class InSubquery(Cond):
    """``<column> IN (SELECT <column> FROM ... WHERE ...)``: how relation filters
    (``Post.author.has(...)``, ``User.posts.any(...)``) are written, with no joins."""

    def __init__(self, field, sub, sub_field):
        self.field, self.sub, self.sub_field = field, sub, sub_field

    def sql(self, c):
        cols = [c.col(self.sub_field)] if self.sub_field is not None else None
        inner = self.sub._compile_select(c, columns=cols, ordered=False)
        return f"{c.col(self.field)} IN ({inner})"


class InJoinTable(Cond):
    """Rows linked through a many-to-many join table to rows matching ``sub``."""

    def __init__(self, field, table, left_col, right_col, sub):
        self.field, self.table, self.left_col, self.right_col, self.sub = field, table, left_col, right_col, sub

    def sql(self, c):
        q = c.d.quote
        target_pk = self.sub.model._meta.pk
        inner = self.sub._compile_select(c, columns=[c.col(target_pk)], ordered=False)
        return (f"{c.col(self.field)} IN (SELECT {q(self.left_col)} FROM {q(self.table)} "
                f"WHERE {q(self.right_col)} IN ({inner}))")


# --------------------------------------------------------------------- ordering

class Order:
    def __init__(self, field, desc=False):
        self.field, self.desc = field, desc

    def sql(self, c):
        return f"{c.col(self.field)} {'DESC' if self.desc else 'ASC'}"


# ------------------------------------------------------------------- aggregates

class Aggregate:
    fn = ""

    def __init__(self, field=None, *, distinct=False):
        self.field, self.distinct = field, distinct

    def resolve(self, model):
        if isinstance(self.field, str):
            self.field = model._meta.field(self.field)
        return self

    def sql(self, c):
        inner = c.col(self.field) if self.field is not None else "*"
        if self.distinct and self.field is not None:
            inner = "DISTINCT " + inner
        return f"{self.fn}({inner})"

    def convert(self, value):
        return value


class Count(Aggregate):
    fn = "COUNT"

    def convert(self, value):
        return int(value or 0)


class Sum(Aggregate):
    fn = "SUM"

    def convert(self, value):
        return self.field.from_db(value) if (value is not None and self.field is not None) else value


class Avg(Aggregate):
    fn = "AVG"

    def convert(self, value):
        return float(value) if value is not None else None


class Min(Sum):
    fn = "MIN"


class Max(Sum):
    fn = "MAX"


# ------------------------------------------------------------------------ pages

class Page(list):
    """One page of results plus ``next`` (a cursor for ``page(after=...)``, or None)."""

    def __init__(self, items, next_cursor, size):
        super().__init__(items)
        self.next = next_cursor
        self.size = size

    @property
    def has_more(self):
        return self.next is not None

    @property
    def items(self):
        return list(self)


def _cursor_value(v):
    import datetime as _dt
    import decimal
    import uuid
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, (decimal.Decimal, uuid.UUID)):
        return str(v)
    return v


# --------------------------------------------------------------------- queryset

_OPS = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


class QuerySet:
    def __init__(self, model, db=None):
        self.model = model
        self._db = db
        self._where = []
        self._order = []
        self._limit = None
        self._offset = None
        self._include = []
        self._for_update = None
        self._distinct = False
        self._group = []
        self._result = None
        self._scoped = False

    # -- building ----------------------------------------------------------
    def _clone(self):
        new = QuerySet.__new__(QuerySet)
        new.__dict__.update(self.__dict__)
        new._where = list(self._where)
        new._order = list(self._order)
        new._include = list(self._include)
        new._group = list(self._group)
        new._result = None
        return new

    def _conditions(self, conds, kwargs):
        meta = self.model._meta
        out = [_as_cond(c) for c in conds]
        for key, value in kwargs.items():
            name, _, op = key.partition("__")
            rel = meta.relations.get(name) or meta.m2m.get(name) or meta.reverse.get(name)
            if rel is not None and op in ("", "eq", "in"):
                out.append(rel.condition(value, many=(op == "in")))
                continue
            field = meta.field(name)
            op = op or "eq"
            if op in _OPS:
                out.append(Compare(field, _OPS[op], value))
            elif op == "in":
                out.append(In(field, list(value)))
            elif op == "not_in":
                out.append(Not(In(field, list(value))))
            elif op == "isnull":
                out.append(Compare(field, "=" if value else "!=", None))
            elif op in ("contains", "icontains", "startswith", "endswith", "like", "ilike"):
                out.append(getattr(field, op)(value))
            elif op == "between":
                low, high = value
                out.append(field.between(low, high))
            else:
                raise ValueError(f"unknown lookup {op!r} in {key!r} (use one of: "
                                 f"{', '.join(list(_OPS) + ['in', 'not_in', 'isnull', 'contains', 'icontains', 'startswith', 'endswith', 'like', 'ilike', 'between'])})")
        return out

    def where(self, *conds, **kwargs):
        """Rows matching every condition: ``where(Post.views > 10, author=user, title__icontains="py")``."""
        new = self._clone()
        new._where.extend(self._conditions(conds, kwargs))
        return new

    filter = where

    def exclude(self, *conds, **kwargs):
        parts = self._conditions(conds, kwargs)
        if not parts:
            return self._clone()
        new = self._clone()
        new._where.append(Not(And(parts) if len(parts) > 1 else parts[0]))
        return new

    def order(self, *keys):
        """``order("-published_at", "title")`` or ``order(Post.views.desc())``. Replaces earlier ordering."""
        new = self._clone()
        new._order = [self._order_item(k) for k in keys]
        return new

    order_by = order

    def _order_item(self, key):
        if isinstance(key, Order):
            return key
        if isinstance(key, Field):
            return Order(key)
        if isinstance(key, str):
            desc = key.startswith("-")
            return Order(self.model._meta.field(key.lstrip("-+")), desc=desc)
        raise TypeError(f"can't order by {key!r}")

    def limit(self, n):
        if n is not None and (not isinstance(n, int) or isinstance(n, bool) or n < 0):
            raise ValueError(f"limit must be a non-negative whole number, not {n!r}")
        new = self._clone()
        new._limit = n
        return new

    def offset(self, n):
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError(f"offset must be a non-negative whole number, not {n!r}")
        new = self._clone()
        new._offset = n
        return new

    def include(self, *names):
        """Load related rows too, with one extra query per relation: ``include("author", "tags", "author.profile")``."""
        new = self._clone()
        for name in names:
            head = name.split(".", 1)[0]
            self.model._meta.relation(head)              # unknown names fail now, not later
            if name not in new._include:
                new._include.append(name)
        return new

    def using(self, db):
        new = self._clone()
        new._db = db
        return new

    def for_update(self, *, skip_locked=False):
        """Lock the selected rows until the transaction ends (Postgres/MySQL; SQLite locks the database)."""
        new = self._clone()
        new._for_update = "skip" if skip_locked else "wait"
        return new

    def distinct(self):
        new = self._clone()
        new._distinct = True
        return new

    def group_by(self, *names):
        new = self._clone()
        new._group = [self.model._meta.field(n) if isinstance(n, str) else n for n in names]
        return new

    def unscoped(self):
        """This query without the Model's row-level policy (for admin/system code)."""
        new = self._clone()
        new._scoped = True
        return new

    # -- SQL -----------------------------------------------------------------
    @property
    def db(self):
        return self._db if self._db is not None else self.model._database()

    def _all_conditions(self):
        conds = list(self._where)
        if not self._scoped:
            scope = self.model._meta.read_scope()
            if scope is not None:
                conds.append(scope)
        return conds

    def _compile_select(self, c, columns=None, ordered=None):
        meta = self.model._meta
        q = c.d.quote
        cols = columns or [c.col(f) for f in meta.columns()]
        sql = "SELECT " + ("DISTINCT " if self._distinct else "") + ", ".join(cols) + f" FROM {q(meta.table)}"
        conds = self._all_conditions()
        if conds:
            sql += " WHERE " + " AND ".join(cond.sql(c) for cond in conds)
        if self._group:
            sql += " GROUP BY " + ", ".join(c.col(f) for f in self._group)
        if ordered is None:
            ordered = columns is None
        order = (self._order or meta.default_order()) if ordered else []
        if order:
            sql += " ORDER BY " + ", ".join(o.sql(c) for o in order)
        sql += c.d.limit_offset(self._limit, self._offset)
        if self._for_update and c.d.name != "sqlite":
            sql += " FOR UPDATE" + (" SKIP LOCKED" if self._for_update == "skip" and c.d.supports_skip_locked else "")
        return sql

    def sql(self):
        """``(sql, params)`` this query would run (for inspection and logging)."""
        c = _Compiler(dialect_of(self.db))
        return self._compile_select(c), c.params

    def __repr__(self):
        try:
            sql, params = self.sql()
        except Exception as exc:  # noqa: BLE001 - repr must not raise
            sql, params = f"<{exc}>", []
        return f"<QuerySet {self.model.__name__}: {sql} {params}>"

    # -- running -------------------------------------------------------------
    def _fetch(self):
        if self._result is None:
            db = self.db
            c = _Compiler(dialect_of(db))
            res = db.execute(self._compile_select(c), c.params)
            objs = [self.model._from_row(r, res.columns, db) for r in res.fetchall()]
            if self._include and objs:
                from .relations import load_includes
                load_includes(self.model, objs, self._include, db)
            self._result = objs
        return self._result

    def all(self):
        return list(self._fetch())

    def __iter__(self):
        return iter(self._fetch())

    def __len__(self):
        return len(self._fetch())

    def __bool__(self):
        return bool(self._fetch())

    def __getitem__(self, index):
        if isinstance(index, slice):
            if self._result is not None or index.step not in (None, 1) or (index.start or 0) < 0 \
                    or (index.stop is not None and index.stop < 0):
                return self._fetch()[index]
            start = index.start or 0
            new = self.offset(start) if start else self._clone()
            if index.stop is not None:
                new = new.limit(max(0, index.stop - start))
            return new.all()
        if self._result is not None or index < 0:
            return self._fetch()[index]
        found = self.offset(index).limit(1).all() if index else self.limit(1).all()
        if not found:
            raise IndexError("query result index out of range")
        return found[0]

    def __eq__(self, other):
        if isinstance(other, (list, tuple)):
            return list(self._fetch()) == list(other)
        return NotImplemented

    __hash__ = None

    def first(self):
        """The first row (by the query's order), or None."""
        found = self.limit(1).all()
        return found[0] if found else None

    def last(self):
        order = self._order or self.model._meta.default_order() or [Order(self.model._meta.pk)]
        return self.order(*[Order(o.field, desc=not o.desc) for o in order]).first()

    def get(self, *conds, **kwargs):
        """The single row matching the conditions, or None. More than one match is an error."""
        if len(conds) == 1 and not kwargs and not isinstance(conds[0], Cond):
            return self.where(self.model._meta.pk == conds[0]).first()
        found = self.where(*conds, **kwargs).limit(2).all()
        if len(found) > 1:
            raise LookupError(f"{self.model.__name__}.get() matched more than one row")
        return found[0] if found else None

    def get_or_404(self, *conds, **kwargs):
        """Like :meth:`get`, but a missing row shows the 404 page."""
        found = self.get(*conds, **kwargs)
        if found is None:
            from pyweb.context import NotFound
            raise NotFound(f"{self.model.__name__} not found")
        return found

    def count(self):
        c = _Compiler(dialect_of(self.db))
        inner = self._clone()
        inner._order, inner._include = [], []
        if self._limit is not None or self._offset or self._distinct or self._group:
            sub = inner._compile_select(c, ordered=False)
            sql = f"SELECT COUNT(*) FROM ({sub}) AS pyweb_count"
        else:
            sql = inner._compile_select(c, columns=["COUNT(*)"], ordered=False)
        return int(self.db.execute(sql, c.params).fetchone()[0])

    def exists(self):
        c = _Compiler(dialect_of(self.db))
        inner = self._clone()
        inner._order, inner._limit = [], 1
        return self.db.execute(inner._compile_select(c, columns=["1"], ordered=False), c.params).fetchone() is not None

    def values(self, *names, **aggregates):
        """Plain dicts: ``values("id", "title")``; with ``group_by``, aggregates per group:
        ``group_by("author").values("author", posts=Count())``."""
        meta = self.model._meta
        fields = [meta.field(n) for n in names] if names else ([] if aggregates else meta.columns())
        aggs = {k: a.resolve(self.model) for k, a in aggregates.items()}
        c = _Compiler(dialect_of(self.db))
        cols = [c.col(f) for f in fields] + [a.sql(c) for a in aggs.values()]
        inner = self._clone()
        inner._include = []
        sql = inner._compile_select(c, columns=cols, ordered=bool(self._order) or not (aggs or self._group))
        res = self.db.execute(sql, c.params)
        keys = list(names) if names else [f.name for f in fields]
        out = []
        for row in res.fetchall():
            item = {k: f.from_db(v) for k, f, v in zip(keys, fields, row)}
            for (k, a), v in zip(aggs.items(), row[len(fields):]):
                item[k] = a.convert(v)
            out.append(item)
        return out

    def pluck(self, name):
        """One column as a list: ``Post.where(...).pluck("id")``."""
        return [row[name] for row in self.values(name)]

    def aggregate(self, **aggregates):
        """Totals over the matching rows: ``aggregate(n=Count(), total=Sum(Order.amount))``."""
        rows = self.values(**aggregates)
        return rows[0] if rows else {k: None for k in aggregates}

    def page(self, size=20, after=None):
        """Cursor pagination: a :class:`Page` of up to ``size`` rows after ``after``
        (the previous page's ``.next``). Stable while rows are added, unlike offsets."""
        if not isinstance(size, int) or size < 1 or size > 1000:
            raise ValueError("page size must be between 1 and 1000")
        meta = self.model._meta
        order = list(self._order or meta.default_order())
        if not any(o.field is meta.pk for o in order):
            order.append(Order(meta.pk, desc=order[-1].desc if order else False))
        query = self.order(*order)
        if after:
            try:
                values = json.loads(base64.urlsafe_b64decode(after.encode() + b"=" * (-len(after) % 4)))
            except (ValueError, TypeError):
                raise ValueError("invalid page cursor") from None
            if not isinstance(values, list) or len(values) != len(order):
                raise ValueError("invalid page cursor")
            options = []
            for i, o in enumerate(order):
                parts = [Compare(order[j].field, "=", order[j].field.coerce(values[j])) for j in range(i)]
                parts.append(Compare(o.field, "<" if o.desc else ">", o.field.coerce(values[i])))
                options.append(And(parts) if len(parts) > 1 else parts[0])
            query = query.where(Or(options) if len(options) > 1 else options[0])
        rows = query.limit(size + 1).all()
        next_cursor = None
        if len(rows) > size:
            rows = rows[:size]
            last = rows[-1]
            vals = []
            for o in order:
                v = getattr(last, o.field.name)
                if v is None:
                    raise ValueError(f"page() can't order by {o.field.name}: it has empty values")
                vals.append(_cursor_value(v))
            next_cursor = base64.urlsafe_b64encode(json.dumps(vals).encode()).decode().rstrip("=")
        return Page(rows, next_cursor, size)

    def update(self, *, all_rows=False, **values):
        """Change matching rows in one statement; returns how many changed."""
        if not values:
            return 0
        if not self._where and not all_rows:
            raise ValueError("update() without where() would change every row; pass all_rows=True if you mean it")
        meta = self.model._meta
        db = self.db
        d = dialect_of(db)
        for f in meta.columns():
            if f.auto_now and f.name not in values:
                values[f.name] = f.initial()
        c = _Compiler(d)
        sets = []
        for key, value in values.items():
            field = meta.assignable(key)
            sets.append(f"{d.quote(field.column)} = {c.value(field, value)}")
        sql = f"UPDATE {d.quote(meta.table)} SET {', '.join(sets)}"
        conds = self._all_conditions()
        if conds:
            sql += " WHERE " + " AND ".join(cond.sql(c) for cond in conds)
        return db.execute(sql, c.params).rowcount

    def delete(self, *, all_rows=False):
        """Delete matching rows in one statement; returns how many were deleted."""
        if not self._where and not all_rows:
            raise ValueError("delete() without where() would delete every row; pass all_rows=True if you mean it")
        meta = self.model._meta
        db = self.db
        d = dialect_of(db)
        c = _Compiler(d)
        sql = f"DELETE FROM {d.quote(meta.table)}"
        conds = self._all_conditions()
        if conds:
            sql += " WHERE " + " AND ".join(cond.sql(c) for cond in conds)
        return db.execute(sql, c.params).rowcount

    def live(self):
        """These rows as a live page variable: open pages update when the table changes."""
        from pyweb.livedata import live
        db = self.db
        sql, params = self.sql()
        meta = self.model._meta
        tables = [meta.table] + [self.model._meta.relation(n.split(".")[0]).target_table() for n in self._include]
        return live(db, sql, params, tables=tables, key=meta.pk.name)
