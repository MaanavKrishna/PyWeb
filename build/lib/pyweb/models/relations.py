"""Relations between Models, loaded explicitly so pages never do N+1 queries.

``author: User`` on ``Post`` is a foreign key (column ``author_id``);
``tags: list[Tag]`` is many-to-many through a join table; ``User`` gets
``posts`` back automatically. Related rows load with ``include()``, one
query per relation however many rows there are::

    posts = Post.where(published=True).include("author", "tags")

Reading ``post.author`` without ``include("author")`` raises
:class:`NotIncluded` while developing, naming the include to add. In
production the row is loaded on demand (and a warning is logged) so a
missed include is slow, never broken.
"""

from __future__ import annotations

import logging

from .fields import Field, MISSING, unwrap_optional
from .query import IN_CHUNK, Compare, Cond, In, InJoinTable, InSubquery, Not

log = logging.getLogger("pyweb.models")
_warned: set = set()

ON_DELETE = {"cascade": "CASCADE", "set null": "SET NULL", "restrict": "RESTRICT", "no action": "NO ACTION"}


class NotIncluded(RuntimeError):
    """A relation was read but not loaded with ``include()``."""


def snake(name):
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and (not name[i - 1].isupper() or (i + 1 < len(name) and name[i + 1].islower())):
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def plural(word):
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


def _strict():
    from . import strict_relations
    return strict_relations()


def _not_loaded(owner, name):
    model = type(owner).__name__
    if _strict():
        raise NotIncluded(
            f"{model}.{name} was not loaded. Add .include({name!r}) to the query that fetched this "
            f"{model} (related rows then load in one query for all rows instead of one query per row).")
    key = (model, name)
    if key not in _warned:
        _warned.add(key)
        log.warning("%s.%s was read without include(%r); loading it row by row (add the include)", model, name, name)


class Relation:
    """Base for the relation descriptors on a Model class."""

    many = False

    def __init__(self, to=None, *, related_name=None):
        self.to = to
        self.related_name = related_name
        self.name = ""
        self.model = None
        self._target = None

    @property
    def target(self):
        if self._target is None:
            from . import resolve_model
            self._target = resolve_model(self.to, self.model)
        return self._target

    def target_table(self):
        return self.target._meta.table

    def __set_name__(self, owner, name):
        if not self.name:
            self.name = name

    def __repr__(self):
        return f"<{type(self).__name__} {getattr(self.model, '__name__', '?')}.{self.name}>"

    __hash__ = object.__hash__


# ------------------------------------------------------------------ foreign key

class ForeignKey(Relation):
    """``author: User = ForeignKey(on_delete="cascade")``. A plain ``author: User``
    annotation means the same. ``on_delete``: ``cascade``, ``set null``, ``restrict``."""

    def __init__(self, to=None, *, on_delete="cascade", related_name=None, nullable=None, index=True,
                 unique=False, column=None, label=None, required=None):
        super().__init__(to, related_name=related_name)
        if on_delete not in ON_DELETE:
            raise ValueError(f"on_delete must be one of {', '.join(ON_DELETE)}")
        self.on_delete = on_delete
        self.nullable = nullable
        self.index = index and not unique
        self.unique = unique
        self.column = column
        self.label = label
        self.required = required
        self.field = None

    def bind(self, model, name, annotation=MISSING):
        self.model = model
        self.name = name
        if annotation is not MISSING and annotation is not None:
            inner, optional = unwrap_optional(annotation)
            if self.to is None:
                self.to = inner
            if self.nullable is None:
                self.nullable = optional
        if self.nullable is None:
            self.nullable = False
        if self.on_delete == "set null":
            self.nullable = True
        self.field = Field(nullable=self.nullable, kind="bigint", column=self.column or f"{name}_id",
                           unique=self.unique, index=self.index, label=self.label, required=self.required)
        self.field.relation = self
        self.field.bind(model, f"{name}_id", MISSING)
        self.field.nullable = self.nullable
        if self.required is None:
            self.field.required = not self.nullable
            self.field.rules.required = not self.nullable
        return self

    # -- descriptor ----------------------------------------------------------
    def _key(self):
        return "__rel_" + self.name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        d = obj.__dict__
        key = self._key()
        if key in d:
            return d[key]
        fk = d.get(self.field.name)
        if fk is None:
            return None
        _not_loaded(obj, self.name)
        found = self.target.query().using(obj._db_used()).get(fk)
        d[key] = found
        return found

    def __set__(self, obj, value):
        from . import Model
        d = obj.__dict__
        if value is None:
            d[self.field.name] = None
            d[self._key()] = None
        elif isinstance(value, Model):
            d[self.field.name] = value.pk
            d[self._key()] = value
        else:
            d[self.field.name] = value
            d.pop(self._key(), None)

    # -- queries ---------------------------------------------------------------
    def condition(self, value, many=False):
        if many:
            return In(self.field, list(value))
        return Compare(self.field, "=", value)

    def __eq__(self, other):
        return self.condition(other)

    def __ne__(self, other):
        return Compare(self.field, "!=", other)

    def in_(self, values):
        return In(self.field, list(values))

    def is_null(self):
        return Compare(self.field, "=", None)

    def not_null(self):
        return Compare(self.field, "!=", None)

    __hash__ = object.__hash__

    def has(self, *conds, **kwargs):
        """Rows whose related row matches: ``Post.where(Post.author.has(User.name == "Ada"))``."""
        return InSubquery(self.field, self.target.query().where(*conds, **kwargs), self.target._meta.pk)

    # -- include ---------------------------------------------------------------
    def load(self, objs, rests, db):
        ids = sorted({o.__dict__.get(self.field.name) for o in objs} - {None}, key=str)
        found = {t.pk: t for t in _fetch_in(self.target, self.target._meta.pk, ids, rests, db)}
        key = self._key()
        for o in objs:
            o.__dict__[key] = found.get(o.__dict__.get(self.field.name))


class OneToOne(ForeignKey):
    """A foreign key that is unique: each target row has at most one of these."""

    def __init__(self, to=None, **kwargs):
        kwargs["unique"] = True
        super().__init__(to, **kwargs)


# --------------------------------------------------------------------- reverse

class Reverse(Relation):
    """``user.posts``: the rows whose foreign key points at this row (made automatically)."""

    def __init__(self, fk):
        super().__init__(fk.model)
        self.fk = fk
        self.single = isinstance(fk, OneToOne)
        self.many = not self.single

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        key = "__rel_" + self.name
        d = obj.__dict__
        if key in d:
            return d[key]
        if self.single:
            if obj.pk is None:
                return None
            _not_loaded(obj, self.name)
            found = self.target.query().using(obj._db_used()).where(Compare(self.fk.field, "=", obj.pk)).first()
            d[key] = found
            return found
        lst = RelatedList(obj, self, loaded=obj.pk is None)
        d[key] = lst
        return lst

    def __set__(self, obj, value):
        raise AttributeError(f"{type(obj).__name__}.{self.name} can't be assigned; "
                             f"set {self.target.__name__}.{self.fk.name} on each row instead")

    def related_query(self, owner):
        return self.target.query().using(owner._db_used()).where(Compare(self.fk.field, "=", owner.pk))

    def condition(self, value, many=False):
        values = list(value) if many else [value]
        from . import Model
        pks = [v.pk if isinstance(v, Model) else v for v in values]
        return InSubquery(self.model._meta.pk, self.target.query().where(In(self.target._meta.pk, pks)), self.fk.field)

    def any(self, *conds, **kwargs):
        """Rows with at least one related row matching: ``User.where(User.posts.any(Post.published == True))``."""
        return InSubquery(self.model._meta.pk, self.target.query().where(*conds, **kwargs), self.fk.field)

    def none(self, *conds, **kwargs):
        return Not(self.any(*conds, **kwargs))

    def load(self, objs, rests, db):
        pks = [o.pk for o in objs if o.pk is not None]
        groups: dict = {}
        for child in _fetch_in(self.target, self.fk.field, pks, rests, db):
            groups.setdefault(child.__dict__.get(self.fk.field.name), []).append(child)
        key = "__rel_" + self.name
        for o in objs:
            kids = groups.get(o.pk, [])
            for kid in kids:
                kid.__dict__["__rel_" + self.fk.name] = o
            if self.single:
                o.__dict__[key] = kids[0] if kids else None
            else:
                o.__dict__[key] = RelatedList(o, self, items=kids, loaded=True)


# ------------------------------------------------------------------ many-to-many

class ManyToMany(Relation):
    """``tags: list[Tag] = ManyToMany()`` (a plain ``list[Tag]`` annotation means the same),
    stored in a join table ``<table>_<name>``."""

    many = True

    def __init__(self, to=None, *, through=None, related_name=None):
        super().__init__(to, related_name=related_name)
        self.through = through

    def bind(self, model, name, annotation=MISSING):
        self.model = model
        self.name = name
        if self.to is None and annotation is not MISSING:
            import typing
            args = typing.get_args(annotation)
            self.to = args[0] if args else None
        return self

    @property
    def table(self):
        return self.through or f"{self.model._meta.table}_{self.name}"

    @property
    def left(self):
        base = snake(self.model.__name__)
        return f"from_{base}_id" if self.target is self.model else f"{base}_id"

    @property
    def right(self):
        base = snake(self.target.__name__)
        return f"to_{base}_id" if self.target is self.model else f"{base}_id"

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        key = "__rel_" + self.name
        d = obj.__dict__
        if key not in d:
            d[key] = RelatedList(obj, self, loaded=obj.pk is None)
        return d[key]

    def __set__(self, obj, values):
        lst = self.__get__(obj)
        if obj.pk is None or not obj.__dict__.get("_persisted"):
            values = list(values)
            obj.__dict__.setdefault("_pending_m2m", {})[self.name] = values
            if not any(isinstance(v, (int, str)) for v in values):
                lst._replace(values)
        else:
            lst.set(values)

    def condition(self, value, many=False):
        from . import Model
        values = list(value) if many else [value]
        pks = [v.pk if isinstance(v, Model) else v for v in values]
        return InJoinTable(self.model._meta.pk, self.table, self.left, self.right,
                           self.target.query().where(In(self.target._meta.pk, pks)))

    def any(self, *conds, **kwargs):
        """Rows linked to at least one row matching: ``Post.where(Post.tags.any(Tag.name == "python"))``."""
        return InJoinTable(self.model._meta.pk, self.table, self.left, self.right,
                           self.target.query().where(*conds, **kwargs))

    def none(self, *conds, **kwargs):
        return Not(self.any(*conds, **kwargs))

    def related_query(self, owner):
        return self.target.query().using(owner._db_used()).where(
            InSubquery(self.target._meta.pk, _JoinRows(self, owner.pk), None))

    def link_rows(self, db, owner_pks):
        d = db.dialect if hasattr(db, "dialect") else None
        from pyweb.db.dialect import dialect_of
        d = dialect_of(db)
        q = d.quote
        out = []
        for i in range(0, len(owner_pks), IN_CHUNK):
            chunk = owner_pks[i:i + IN_CHUNK]
            sql = (f"SELECT {q(self.left)}, {q(self.right)} FROM {q(self.table)} "
                   f"WHERE {q(self.left)} IN ({', '.join('?' for _ in chunk)})")
            out.extend(tuple(r) for r in db.execute(sql, chunk).fetchall())
        return out

    def load(self, objs, rests, db):
        pks = [o.pk for o in objs if o.pk is not None]
        links = self.link_rows(db, pks)
        targets = {t.pk: t for t in _fetch_in(self.target, self.target._meta.pk, sorted({r for _, r in links}, key=str), rests, db)}
        order = {pk: i for i, pk in enumerate(targets)}
        groups: dict = {}
        for left, right in links:
            if right in targets:
                groups.setdefault(left, []).append(targets[right])
        key = "__rel_" + self.name
        for o in objs:
            items = sorted(groups.get(o.pk, []), key=lambda t: order[t.pk])
            o.__dict__[key] = RelatedList(o, self, items=items, loaded=True)

    def add(self, owner, targets):
        from pyweb.db.dialect import dialect_of
        db = owner._db_used()
        d = dialect_of(db)
        q = d.quote
        for t in targets:
            pk = getattr(t, "pk", t)
            if pk is None:
                raise ValueError(f"save the {self.target.__name__} before linking it")
            db.execute(f"{d.insert_ignore} {q(self.table)} ({q(self.left)}, {q(self.right)}) VALUES (?, ?)"
                       f"{d.insert_ignore_suffix}", (owner.pk, pk))

    def remove(self, owner, targets):
        from pyweb.db.dialect import dialect_of
        db = owner._db_used()
        q = dialect_of(db).quote
        pks = [getattr(t, "pk", t) for t in targets]
        if pks:
            db.execute(f"DELETE FROM {q(self.table)} WHERE {q(self.left)} = ? AND {q(self.right)} IN "
                       f"({', '.join('?' for _ in pks)})", [owner.pk, *pks])

    def clear(self, owner):
        from pyweb.db.dialect import dialect_of
        db = owner._db_used()
        q = dialect_of(db).quote
        db.execute(f"DELETE FROM {q(self.table)} WHERE {q(self.left)} = ?", (owner.pk,))


class _JoinRows:
    """The ``SELECT right FROM join WHERE left = ?`` subquery behind ``post.tags.query()``."""

    def __init__(self, m2m, owner_pk):
        self.m2m, self.owner_pk = m2m, owner_pk

    def _compile_select(self, c, columns=None, ordered=None):
        q = c.d.quote
        c.params.append(self.owner_pk)
        return f"SELECT {q(self.m2m.right)} FROM {q(self.m2m.table)} WHERE {q(self.m2m.left)} = ?"


class ManyToManyReverse(Relation):
    """``tag.posts``: the other side of a many-to-many (made automatically)."""

    many = True

    def __init__(self, m2m):
        super().__init__(m2m.model)
        self.m2m = m2m

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        key = "__rel_" + self.name
        d = obj.__dict__
        if key not in d:
            d[key] = RelatedList(obj, self, loaded=obj.pk is None)
        return d[key]

    def related_query(self, owner):
        m = self.m2m

        class _Rows(_JoinRows):
            def _compile_select(self, c, columns=None, ordered=None):
                q = c.d.quote
                c.params.append(owner.pk)
                return f"SELECT {q(m.left)} FROM {q(m.table)} WHERE {q(m.right)} = ?"
        return self.target.query().using(owner._db_used()).where(InSubquery(self.target._meta.pk, _Rows(m, owner.pk), None))

    def condition(self, value, many=False):
        from . import Model
        values = list(value) if many else [value]
        pks = [v.pk if isinstance(v, Model) else v for v in values]
        return InJoinTable(self.model._meta.pk, self.m2m.table, self.m2m.right, self.m2m.left,
                           self.target.query().where(In(self.target._meta.pk, pks)))

    def any(self, *conds, **kwargs):
        return InJoinTable(self.model._meta.pk, self.m2m.table, self.m2m.right, self.m2m.left,
                           self.target.query().where(*conds, **kwargs))

    def load(self, objs, rests, db):
        from pyweb.db.dialect import dialect_of
        m = self.m2m
        q = dialect_of(db).quote
        pks = [o.pk for o in objs if o.pk is not None]
        links = []
        for i in range(0, len(pks), IN_CHUNK):
            chunk = pks[i:i + IN_CHUNK]
            sql = (f"SELECT {q(m.right)}, {q(m.left)} FROM {q(m.table)} "
                   f"WHERE {q(m.right)} IN ({', '.join('?' for _ in chunk)})")
            links.extend(tuple(r) for r in db.execute(sql, chunk).fetchall())
        targets = {t.pk: t for t in _fetch_in(self.target, self.target._meta.pk, sorted({r for _, r in links}, key=str), rests, db)}
        order = {pk: i for i, pk in enumerate(targets)}
        groups: dict = {}
        for left, right in links:
            if right in targets:
                groups.setdefault(left, []).append(targets[right])
        key = "__rel_" + self.name
        for o in objs:
            items = sorted(groups.get(o.pk, []), key=lambda t: order[t.pk])
            o.__dict__[key] = RelatedList(o, self, items=items, loaded=True)

    def add(self, owner, targets):
        for t in targets:
            self.m2m.add(t, [owner])

    def remove(self, owner, targets):
        for t in targets:
            self.m2m.remove(t, [owner])

    def clear(self, owner):
        from pyweb.db.dialect import dialect_of
        db = owner._db_used()
        q = dialect_of(db).quote
        db.execute(f"DELETE FROM {q(self.m2m.table)} WHERE {q(self.m2m.right)} = ?", (owner.pk,))


# ------------------------------------------------------------------ the lists

class RelatedList(list):
    """The rows on the "many" side of a relation (``user.posts``, ``post.tags``).

    Reading it needs ``include()``; changing it (``add``, ``remove``, ``set``,
    ``clear``, ``create``) writes straight to the database and never does.
    ``.query()`` is a normal query over the related rows.
    """

    def __init__(self, owner, relation, *, items=(), loaded=False):
        super().__init__(items)
        self._owner = owner
        self._relation = relation
        self._loaded = loaded

    def _ensure(self):
        if not self._loaded:
            _not_loaded(self._owner, self._relation.name)
            list.extend(self, self._relation.related_query(self._owner).all())
            self._loaded = True

    def _replace(self, items):
        list.clear(self)
        list.extend(self, items)
        self._loaded = True

    def __iter__(self):
        self._ensure()
        return list.__iter__(self)

    def __len__(self):
        self._ensure()
        return list.__len__(self)

    def __getitem__(self, i):
        self._ensure()
        return list.__getitem__(self, i)

    def __bool__(self):
        self._ensure()
        return list.__len__(self) > 0

    def __contains__(self, item):
        self._ensure()
        return list.__contains__(self, item)

    def __eq__(self, other):
        self._ensure()
        return list.__eq__(self, other)

    __hash__ = None

    def __repr__(self):
        if not self._loaded:
            return f"<{type(self._owner).__name__}.{self._relation.name} (not loaded)>"
        return list.__repr__(self)

    def query(self):
        """A query over the related rows (always allowed: it's one explicit query)."""
        return self._relation.related_query(self._owner)

    def _require_saved(self):
        if self._owner.pk is None:
            raise ValueError(f"save the {type(self._owner).__name__} before changing {self._relation.name}")

    def add(self, *items):
        self._require_saved()
        rel = self._relation
        if isinstance(rel, Reverse):
            for item in items:
                setattr(item, rel.fk.name, self._owner)
                item.save()
        else:
            rel.add(self._owner, items)
        if self._loaded:
            for item in items:
                if not list.__contains__(self, item):
                    list.append(self, item)

    def remove(self, *items):
        self._require_saved()
        rel = self._relation
        if isinstance(rel, Reverse):
            for item in items:
                if not rel.fk.nullable:
                    raise ValueError(f"{rel.target.__name__}.{rel.fk.name} can't be empty; delete the row instead")
                setattr(item, rel.fk.name, None)
                item.save()
        else:
            rel.remove(self._owner, items)
        if self._loaded:
            for item in items:
                if list.__contains__(self, item):
                    list.remove(self, item)

    def clear(self):
        self._require_saved()
        rel = self._relation
        if isinstance(rel, Reverse):
            self.remove(*self.query().all())
        else:
            rel.clear(self._owner)
        list.clear(self)
        self._loaded = True

    def set(self, items):
        """Make the relation hold exactly ``items``."""
        self._require_saved()
        items = list(items)
        rel = self._relation
        if isinstance(rel, Reverse):
            keep = {i.pk for i in items}
            stale = [r for r in self.query().all() if r.pk not in keep]
            if stale:
                self.remove(*stale)
            self.add(*items)
        else:
            rel.clear(self._owner)
            rel.add(self._owner, items)
        if all(hasattr(i, "_meta") or hasattr(type(i), "_meta") for i in items) and \
                not any(isinstance(i, (int, str)) for i in items):
            self._replace(items)
        else:                                  # set by id: load the rows when read
            list.clear(self)
            self._loaded = False

    def create(self, **values):
        """Create a related row and link it: ``user.posts.create(title="Hi")``."""
        self._require_saved()
        rel = self._relation
        if isinstance(rel, Reverse):
            values[rel.fk.name] = self._owner
            item = rel.target.create(**values)
        else:
            item = rel.target.create(**values)
            rel.add(self._owner, [item])
        if self._loaded:
            list.append(self, item)
        return item


# --------------------------------------------------------------------- loading

def _fetch_in(model, field, values, rests, db):
    out = []
    values = list(values)
    for i in range(0, len(values), IN_CHUNK):
        qs = model.query().using(db).where(In(field, values[i:i + IN_CHUNK]))
        if rests:
            qs = qs.include(*rests)
        out.extend(qs.all())
    return out


def load_includes(model, objs, paths, db):
    """Fill the relations named in ``paths`` (``"author"``, ``"author.profile"``) on ``objs``."""
    grouped: dict = {}
    for path in paths:
        head, _, rest = path.partition(".")
        grouped.setdefault(head, [])
        if rest:
            grouped[head].append(rest)
    for head, rests in grouped.items():
        model._meta.relation(head).load(objs, rests, db)


__all__ = ["ForeignKey", "OneToOne", "ManyToMany", "Reverse", "ManyToManyReverse", "RelatedList",
           "NotIncluded", "Cond", "snake", "plural", "load_includes"]
