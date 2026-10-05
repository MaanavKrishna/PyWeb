"""0.5 data layer: Models on pyweb.db, rules, queries, relations, transactions."""

import datetime as dt
import decimal
import logging
import uuid

import pytest

from pyweb import models as M
from pyweb.context import NotFound
from pyweb.db import QUERY_HOOKS, SQLiteDB, atomic, request_scope
from pyweb.models import (Avg, Count, Email, Field, ForeignKey, Index, ManyToMany, Max, Min, Model, NotIncluded,
                          OneToOne, Slug, Sum, Text, URL, ValidationError, validates)


# ------------------------------------------------------------------ models

class Author(Model):
    email: Email = Field(unique=True)
    name: str = Field(max=40)
    bio: Text = ""
    rating: float = 0.0


class Label(Model):
    name: str = Field(unique=True, max=30)


class Article(Model):
    title: str = Field(min=3, max=120)
    slug: Slug | None = None
    author: Author
    editor: Author | None = ForeignKey(related_name="edited", on_delete="set null")
    labels: list[Label] = []
    views: int = Field(0, min=0)
    status: str = Field("draft", choices=["draft", "live"])
    price: decimal.Decimal | None = None
    meta: dict = Field(default_factory=dict)
    published_at: dt.datetime | None = None
    created_at: dt.datetime = Field(auto_now_add=True)
    updated_at: dt.datetime = Field(auto_now=True)
    secret: str = Field("", private=True)

    class Meta:
        ordering = ["-id"]
        indexes = [Index("author", "published_at")]

    @validates("title")
    def no_shouting(self, value):
        if value.isupper():
            raise ValueError("can't be all capitals")


class Profile(Model):
    owner: Author = OneToOne(related_name="profile")
    website: URL | None = None


class Person(Model):
    name: str
    follows: list["Person"] = ManyToMany(related_name="followers")


class Everything(Model):
    i: int = 0
    s: str = ""
    f: float = 0.0
    b: bool = False
    d: decimal.Decimal | None = None
    when: dt.datetime | None = None
    day: dt.date | None = None
    at: dt.time | None = None
    data: dict | None = None
    items: list | None = None
    blob: bytes | None = None
    key: uuid.UUID | None = None


@pytest.fixture(autouse=True)
def db(monkeypatch):
    database = SQLiteDB(":memory:")
    monkeypatch.setattr(M._state, "db", None)
    M.use_database(database, auto_create=True)
    yield database
    M._state.db = None
    database.close()


@pytest.fixture
def queries():
    seen = []

    def hook(db, sql, params, ms, rowcount):
        seen.append(sql)
    QUERY_HOOKS.append(hook)
    yield seen
    QUERY_HOOKS.remove(hook)


def make(n_articles=3):
    ada = Author.create(email="ada@example.com", name="Ada")
    bob = Author.create(email="bob@example.com", name="Bob")
    py, web = Label.create(name="python"), Label.create(name="web")
    arts = [Article.create(title=f"Post {i}", author=ada if i % 2 == 0 else bob, views=i * 10,
                           labels=[py] if i % 2 == 0 else [py, web]) for i in range(n_articles)]
    return ada, bob, py, web, arts


# ------------------------------------------------------------------ fields

def test_every_type_round_trips():
    key = uuid.uuid4()
    when = dt.datetime(2026, 5, 1, 12, 30, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    row = Everything.create(i=7, s="text", f=1.5, b=True, d=decimal.Decimal("12.34"), when=when,
                            day=dt.date(2026, 1, 2), at=dt.time(9, 15), data={"a": [1, 2]}, items=[1, "x"],
                            blob=b"\x00\x01", key=key)
    got = Everything.get(row.id)
    assert (got.i, got.s, got.f, got.b) == (7, "text", 1.5, True)
    assert got.d == decimal.Decimal("12.34") and isinstance(got.d, decimal.Decimal)
    assert got.when == when and got.when.tzinfo is not None          # stored in UTC, same instant
    assert got.when.utcoffset() == dt.timedelta(0)
    assert got.day == dt.date(2026, 1, 2) and got.at == dt.time(9, 15)
    assert got.data == {"a": [1, 2]} and got.items == [1, "x"]
    assert got.blob == b"\x00\x01" and got.key == key
    empty = Everything.get(Everything.create().id)
    assert empty.d is None and empty.data is None and empty.b is False


def test_naive_datetimes_are_utc():
    row = Everything.create(when=dt.datetime(2026, 1, 1, 8, 0))
    assert Everything.get(row.id).when == dt.datetime(2026, 1, 1, 8, 0, tzinfo=dt.timezone.utc)


def test_defaults_and_timestamps():
    ada = Author.create(email="a@example.com", name="Ada")
    a = Article.create(title="Hello", author=ada)
    assert a.status == "draft" and a.views == 0 and a.meta == {}
    assert a.created_at is not None and a.updated_at is not None
    b = Article.create(title="Other", author=ada)
    b.meta["x"] = 1
    assert a.meta == {}                                  # default_factory: not shared
    first_update = a.updated_at
    a.title = "Hello again"
    a.save()
    assert a.updated_at >= first_update
    assert Article.get(a.id).created_at == a.created_at


def test_json_changes_in_place_are_saved():
    ada = Author.create(email="a@example.com", name="Ada")
    a = Article.create(title="Hello", author=ada)
    a.meta["seen"] = True
    a.save()
    assert Article.get(a.id).meta == {"seen": True}


# ------------------------------------------------------------------- rules

def test_rules_are_checked_on_save():
    ada = Author.create(email="a@example.com", name="Ada")
    with pytest.raises(ValidationError) as err:
        Article.create(title="Hi", author=ada, views=-1, status="gone")
    assert err.value.errors == {"title": "must be at least 3 characters", "views": "must be at least 0",
                                "status": "must be one of: draft, live"}
    with pytest.raises(ValidationError) as err:
        Article.create(title="LOUD TITLE", author=ada)
    assert err.value.errors == {"title": "can't be all capitals"}
    with pytest.raises(ValidationError) as err:
        Article.create(title="Fine title")
    assert err.value.errors == {"author": "is required"}
    with pytest.raises(ValidationError) as err:
        Author.create(email="not-an-email", name="x" * 41)
    assert set(err.value.errors) == {"email", "name"}
    with pytest.raises(ValidationError) as err:
        Article.create(title="Fine", author=ada, slug="Not A Slug")
    assert "slug" in err.value.errors


def test_values_are_coerced_and_bad_types_reported():
    ada = Author.create(email="a@example.com", name="Ada")
    a = Article.create(title="Hello", author=ada, views="12", price="9.50")
    assert a.views == 12 and a.price == decimal.Decimal("9.50")
    with pytest.raises(ValidationError) as err:
        Article.create(title="Hello", author=ada, views="many")
    assert err.value.errors == {"views": "must be a whole number"}


def test_unique_violation_becomes_a_field_error():
    Author.create(email="a@example.com", name="Ada")
    with pytest.raises(ValidationError) as err:
        Author.create(email="a@example.com", name="Other")
    assert err.value.errors == {"email": "is already taken"}


def test_model_clean_hook():
    class Range(Model):
        low: int
        high: int

        def clean(self):
            if self.low > self.high:
                raise ValidationError({"high": "must be at least low"})
    with pytest.raises(ValidationError) as err:
        Range.create(low=5, high=1)
    assert err.value.errors == {"high": "must be at least low"}
    assert Range.create(low=1, high=5).id


# --------------------------------------------------------------------- CRUD

def test_crud_and_partial_updates(queries):
    ada = Author.create(email="a@example.com", name="Ada")
    got = Author.get(ada.id)
    assert got == ada and got.name == "Ada"
    queries.clear()
    got.name = "Ada L."
    got.save()
    update = [q for q in queries if q.startswith("UPDATE")]
    assert len(update) == 1 and '"name" = ?' in update[0] and '"email"' not in update[0]
    queries.clear()
    got.save()
    assert not [q for q in queries if q.startswith("UPDATE")]      # nothing changed, nothing written
    got.update(rating=4.5)
    assert Author.get(ada.id).rating == 4.5
    ada.refresh()
    assert ada.name == "Ada L."
    got.delete()
    assert Author.get(ada.id) is None and Author.count() == 0


def test_unknown_fields_are_rejected():
    with pytest.raises(TypeError, match="unknown fields"):
        Author(email="a@example.com", name="A", is_admin=True)


def test_private_fields_stay_out_of_dicts():
    ada = Author.create(email="a@example.com", name="Ada")
    a = Article.create(title="Hello", author=ada, secret="s3cret")
    assert "secret" not in a.to_dict() and a.to_dict(include_private=True)["secret"] == "s3cret"
    from pyweb.ssr import to_jsonable
    assert "secret" not in to_jsonable(a)


def test_rows_read_like_dicts_for_0_4_code():
    ada = Author.create(email="a@example.com", name="Ada")
    assert ada["name"] == "Ada" and "email" in ada and "missing" not in ada
    assert dict(ada)["email"] == "a@example.com"


# ------------------------------------------------------------------ queries

def test_conditions_and_lookups():
    _, _, _, _, arts = make(5)
    assert Article.where(Article.views > 20).count() == 2
    assert Article.where(Article.views >= 20, Article.views < 40).count() == 2
    assert Article.where((Article.views == 0) | (Article.views == 40)).count() == 2
    assert Article.where(~(Article.views == 0)).count() == 4
    assert Article.where(views__gte=30).count() == 2
    assert Article.where(views__in=[0, 10]).count() == 2
    assert Article.where(views__in=[]).count() == 0
    assert Article.where(title__icontains="POST 3").count() == 1
    assert Article.where(title__startswith="Post").count() == 5
    assert Article.where(slug__isnull=True).count() == 5
    assert Article.where(Article.views.between(10, 30)).count() == 3
    assert Article.exclude(views=0).count() == 4
    assert Article.where(Article.views.not_in([0, 10])).count() == 3


def test_like_wildcards_in_values_are_literal():
    ada = Author.create(email="a@example.com", name="Ada")
    Article.create(title="100% done", author=ada)
    Article.create(title="1000 done", author=ada)
    Article.create(title="a_b title", author=ada)
    Article.create(title="axb title", author=ada)
    assert Article.where(title__contains="%").pluck("title") == ["100% done"]
    assert Article.where(title__contains="a_b").pluck("title") == ["a_b title"]


def test_ordering_slicing_and_firsts():
    make(5)
    assert Article.query().pluck("views") == [40, 30, 20, 10, 0]           # Meta.ordering = -id
    assert Article.order("views").pluck("views") == [0, 10, 20, 30, 40]
    assert Article.order(Article.views.desc()).first().views == 40
    assert Article.order("views").last().views == 40
    q = Article.order("views")
    assert [a.views for a in q[1:3]] == [10, 20] and q[0].views == 0
    assert [a.views for a in q.limit(2).offset(3)] == [30, 40]
    with pytest.raises(IndexError):
        q[99]


def test_queries_are_immutable_and_lazy(queries):
    make(4)
    queries.clear()
    base = Article.where(Article.views > 0)
    high = base.where(Article.views > 20)
    assert not queries                                   # nothing ran yet
    assert base.count() == 3 and high.count() == 1
    rows = base.order("views")
    list(rows)
    n = len(queries)
    assert len(rows) == 3 and rows[0].views == 10 and len(queries) == n   # cached after the first run


def test_get_semantics():
    _, _, _, _, arts = make(3)
    assert Article.get(arts[0].id) == arts[0]
    assert Article.get(9999) is None
    with pytest.raises(LookupError, match="more than one"):
        Article.get(Article.views >= 0)
    with pytest.raises(NotFound):
        Article.get_or_404(9999)


def test_values_aggregates_and_groups():
    ada, bob, *_ = make(4)
    assert Article.query().aggregate(n=Count(), total=Sum(Article.views), top=Max("views"), low=Min("views"),
                                     mean=Avg(Article.views)) == {"n": 4, "total": 60, "top": 30, "low": 0, "mean": 15.0}
    groups = Article.query().group_by("author").order("author").values("author", n=Count(), total=Sum("views"))
    assert groups == [{"author": ada.id, "n": 2, "total": 20}, {"author": bob.id, "n": 2, "total": 40}]
    assert Article.where(views__gt=100).aggregate(n=Count(), total=Sum("views")) == {"n": 0, "total": None}
    assert Article.order("views").values("title", "views")[0] == {"title": "Post 0", "views": 0}
    assert Article.query().distinct().values("status") == [{"status": "draft"}]


def test_bulk_update_and_delete():
    make(4)
    assert Article.where(Article.views >= 20).update(status="live") == 2
    assert Article.where(status="live").count() == 2
    with pytest.raises(ValueError, match="every row"):
        Article.query().update(status="draft")
    assert Article.query().update(status="draft", all_rows=True) == 4
    assert Article.where(views=0).delete() == 1
    with pytest.raises(ValueError, match="every row"):
        Article.query().delete()
    assert Article.count() == 3


def test_unknown_names_are_errors_not_sql():
    with pytest.raises(ValueError, match="no field 'nope'"):
        Article.where(nope=1)
    with pytest.raises(ValueError, match="no field"):
        Article.order("title; DROP TABLE articles")
    with pytest.raises(ValueError, match="unknown lookup"):
        Article.where(views__sql="1")
    with pytest.raises(ValueError, match="no relation"):
        Article.include("comments")


def test_values_are_always_parameters(queries):
    ada = Author.create(email="a@example.com", name="Ada")
    evil = "x' OR '1'='1"
    Article.create(title=evil, author=ada)
    assert Article.where(title=evil).count() == 1 and Article.where(title="x").count() == 0
    assert not any(evil in q for q in queries)


def test_conditions_cannot_be_used_as_booleans():
    with pytest.raises(TypeError, match="combine conditions with &"):
        if Article.views > 1:
            pass
    assert Article.views in [Article.title, Article.views]            # identity checks still work


def test_cursor_pages_walk_everything_once():
    ada = Author.create(email="a@example.com", name="Ada")
    for i in range(23):
        Article.create(title=f"Item {i:02d}", author=ada, views=i % 5)
    seen, cursor = [], None
    while True:
        page = Article.order("-views").page(size=5, after=cursor)
        seen += [a.id for a in page]
        if not page.has_more:
            break
        cursor = page.next
    assert len(seen) == 23 and len(set(seen)) == 23
    views = [Article.get(i).views for i in seen]
    assert views == sorted(views, reverse=True)
    first = Article.order("-views").page(size=5)
    Article.create(title="New one", author=ada, views=4)        # a new row doesn't shift the next page
    second = Article.order("-views").page(size=5, after=first.next)
    assert not set(a.id for a in first) & set(a.id for a in second)
    with pytest.raises(ValueError, match="cursor"):
        Article.query().page(after="not-a-cursor!")


# ---------------------------------------------------------------- relations

def test_include_loads_each_relation_in_one_query(queries):
    ada = Author.create(email="a@example.com", name="Ada")
    tags = [Label.create(name=f"t{i}") for i in range(3)]
    for i in range(50):
        Article.create(title=f"Post {i}", author=ada, labels=tags[: i % 4])
    queries.clear()
    rows = Article.query().include("author", "labels", "editor").all()
    # the rows, the authors, the label links, the labels; no editors are set, so no query for them
    assert len(queries) == 4
    assert all(r.author.name == "Ada" for r in rows)
    assert sum(len(r.labels) for r in rows) == sum(min(i % 4, 3) for i in range(50))
    assert all(r.editor is None for r in rows)
    assert len(queries) == 4


def test_reading_a_relation_without_include_is_an_error_while_developing():
    ada, *_ = make(1)
    art = Article.query().first()
    with pytest.raises(NotIncluded, match=r"\.include\('author'\)"):
        _ = art.author
    with pytest.raises(NotIncluded, match="labels"):
        list(art.labels)
    with pytest.raises(NotIncluded, match="articles"):
        len(Author.get(ada.id).articles)
    assert art.author_id == ada.id                       # the raw id is always there


def test_production_loads_missed_relations_with_a_warning(monkeypatch, caplog):
    make(2)
    monkeypatch.setenv("PYWEB_STRICT_RELATIONS", "0")
    art = Article.query().first()
    with caplog.at_level(logging.WARNING, logger="pyweb.models"):
        assert art.author.name in ("Ada", "Bob")
        assert len(art.labels) >= 1
    assert "without include" in caplog.text


def test_reverse_and_nested_includes():
    ada, bob, py, web, arts = make(4)
    authors = Author.order("name").include("articles", "articles.labels").all()
    assert [a.name for a in authors] == ["Ada", "Bob"]
    assert sorted(x.title for x in authors[0].articles) == ["Post 0", "Post 2"]
    assert all(lbl.name for art in authors[1].articles for lbl in art.labels)
    labels = Label.order("name").include("articles").all()
    assert len(labels[0].articles) == 4 and len(labels[1].articles) == 2
    assert authors[0].articles[0].author is authors[0]          # back-reference filled, no query


def test_relation_filters_without_joins():
    ada, bob, py, web, arts = make(4)
    assert sorted(Article.where(Article.author.has(Author.name == "Bob")).pluck("title")) == ["Post 1", "Post 3"]
    assert Article.where(author=ada).count() == 2
    assert Article.where(author__in=[ada, bob]).count() == 4
    assert Article.where(Article.labels.any(Label.name == "web")).count() == 2
    assert Article.where(Article.labels.none(Label.name == "web")).count() == 2
    assert Article.where(labels=web).count() == 2
    assert Author.where(Author.articles.any(Article.views > 25)).pluck("name") == ["Bob"]
    assert Label.where(Label.articles.any(Article.author == ada)).count() == 1


def test_related_lists_write_through():
    ada, bob, py, web, arts = make(1)
    art = arts[0]
    art.labels.add(web)
    assert sorted(lbl.name for lbl in art.labels.query()) == ["python", "web"]
    art.labels.remove(py)
    assert art.labels.query().pluck("name") == ["web"]
    art.labels.set([py, web])
    assert art.labels.query().count() == 2 and len(art.labels) == 2
    art.labels.clear()
    assert art.labels.query().count() == 0
    new = art.labels.create(name="fresh")
    assert new.id and art.labels.query().pluck("name") == ["fresh"]
    post = ada.articles.create(title="Via author")
    assert post.author_id == ada.id
    assert web.articles.query().count() == 0


def test_self_referential_many_to_many():
    a, b, c = Person.create(name="a"), Person.create(name="b"), Person.create(name="c")
    a.follows.add(b, c)
    b.follows.add(c)
    people = Person.order("name").include("follows", "followers").all()
    assert [p.name for p in people[0].follows] == ["b", "c"]
    assert sorted(p.name for p in people[2].followers) == ["a", "b"]


def test_one_to_one_and_on_delete_rules():
    ada, bob, py, web, arts = make(2)
    prof = Profile.create(owner=ada, website="https://ada.dev")
    loaded = Author.where(id=ada.id).include("profile").first()
    assert loaded.profile == prof
    with pytest.raises(ValidationError):
        Profile.create(owner=ada)                               # one profile per author
    art = arts[0]
    art.editor = bob
    art.save()
    bob.delete()                                                 # set null for editor, cascade for author
    remaining = Article.include("editor").all()
    assert [a.editor for a in remaining] == [None] and remaining[0].author_id == ada.id
    ada.delete()
    assert Article.count() == 0 and Profile.count() == 0


def test_assigning_relations():
    ada = Author.create(email="a@example.com", name="Ada")
    art = Article(title="Hello", author=ada.id)
    art.save()
    assert art.author_id == ada.id
    unsaved = Author(email="b@example.com", name="Bob")
    with pytest.raises(ValueError, match="save the Author"):
        Article(title="Hi there", author=unsaved).save()
    art.author = None
    with pytest.raises(ValidationError):
        art.save()


def test_unsaved_rows_have_empty_relations():
    art = Article(title="Draft")
    assert list(art.labels) == []


# -------------------------------------------------------------- writes, upsert

def test_get_or_create_upsert_bulk():
    ada, created = Author.get_or_create(email="a@example.com", defaults={"name": "Ada"})
    again, created2 = Author.get_or_create(email="a@example.com", defaults={"name": "Other"})
    assert created and not created2 and again == ada
    Label.upsert(key="name", name="python")
    Label.upsert(key="name", name="python")
    assert Label.count() == 1
    made = Label.bulk_create([{"name": "a"}, {"name": "b"}])
    assert all(m.id for m in made) and Label.count() == 3


# ------------------------------------------------------------- transactions

def test_nested_transactions_are_savepoints(db):
    ada = Author.create(email="a@example.com", name="Ada")
    with db.transaction():
        Article.create(title="Kept", author=ada)
        with pytest.raises(RuntimeError):
            with db.transaction():
                Article.create(title="Dropped", author=ada)
                raise RuntimeError("inner")
        Article.create(title="Also kept", author=ada)
    assert sorted(Article.query().pluck("title")) == ["Also kept", "Kept"]
    with pytest.raises(RuntimeError):
        with db.transaction():
            Article.create(title="Outer", author=ada)
            raise RuntimeError("outer")
    assert Article.count() == 2


def test_request_scope_is_one_transaction():
    ada = Author.create(email="a@example.com", name="Ada")
    with pytest.raises(ValidationError):
        with request_scope():
            Article.create(title="First", author=ada)
            Article.create(title="no", author=ada)              # fails validation: undo the first too
    assert Article.count() == 0
    with request_scope():
        Article.create(title="First", author=ada)
        assert Article.count() == 1                               # reads its own write
    assert Article.count() == 1


def test_atomic_decorator_and_opt_out():
    ada = Author.create(email="a@example.com", name="Ada")

    @atomic
    def two_writes(fail):
        Article.create(title="One", author=ada)
        if fail:
            raise RuntimeError("stop")
        Article.create(title="Two", author=ada)

    with pytest.raises(RuntimeError):
        two_writes(True)
    assert Article.count() == 0
    two_writes(False)
    assert Article.count() == 2

    @atomic(False)
    def loose():
        pass
    assert loose.__pyweb_atomic__ is False


def test_rpc_calls_run_in_one_transaction():
    from pyweb.runtime.server import Request, Server
    ada = Author.create(email="a@example.com", name="Ada")

    def publish(title: str):
        Article.create(title=title, author=ada)
        Article.create(title="x", author=ada)                     # invalid: undo the first

    def publish_ok(title: str):
        return Article.create(title=title, author=ada).id

    srv = Server({"pages": {}, "rpc": []}, rate_limit=None, rpc_timeout=5)
    srv.register_rpc(publish)
    srv.register_rpc(publish_ok)

    def call(name, args):
        import json
        resp = srv.handle(Request("POST", f"/__pyweb/rpc/{name}", {"Content-Type": "application/json"},
                                  json.dumps({"args": args}).encode()))
        return resp.status, json.loads(resp.body)

    status, body = call("publish", {"title": "Hello"})
    assert status == 422 and body["error"]["details"]["errors"] == {"title": "must be at least 3 characters"}
    assert Article.count() == 0
    status, body = call("publish_ok", {"title": "Hello"})
    assert status == 200 and Article.get(body["result"]).title == "Hello"


# ------------------------------------------------------------------- binding

def test_bind_overrides_default_database():
    other = SQLiteDB(":memory:")
    M._auto.setdefault(other, set())

    class Note(Model):
        text: str
    Note.bind(other)
    Note.create(text="x")
    assert other.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1


def test_implicit_database_in_development_only(monkeypatch):
    monkeypatch.setattr(M._state, "db", None)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    class Scratch(Model):
        text: str
    Scratch.create(text="kept in memory")
    assert Scratch.count() == 1
    monkeypatch.setenv("PYWEB_ENV", "production")

    class Scratch2(Model):
        text: str
    with pytest.raises(RuntimeError, match="not bound"):
        Scratch2.count()


def test_database_url_is_used(monkeypatch, tmp_path):
    monkeypatch.setattr(M._state, "db", None)
    url = f"sqlite:///{tmp_path / 'env.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    assert M.database() is M.database() and "env.db" in M.database().path


def test_app_database_binds_models(tmp_path, monkeypatch):
    from pyweb import App
    monkeypatch.setattr(M._state, "db", None)
    app = App(database=f"sqlite:///{tmp_path / 'app.db'}")
    assert M.database() is app.db


def test_configure_still_works_but_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(M._state, "db", None)

    class Legacy(Model):
        name: str
        age: int
    with pytest.warns(DeprecationWarning):
        Model.configure(str(tmp_path / "legacy.db"))
    Legacy.create(name="a", age=1)
    assert Legacy.where(name="a")[0]["age"] == 1 and Legacy.table() == "legacies"


def test_schema_sql_per_dialect():
    from pyweb.db.dialect import MYSQL, POSTGRES
    pg = Article.schema_sql(POSTGRES)
    assert "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY" in pg and "TIMESTAMPTZ" in pg
    assert 'VARCHAR(120)' in pg and "JSONB" in pg and "NUMERIC(12, 2)" in pg
    my = Article.schema_sql(MYSQL)
    assert "`title` VARCHAR(120) NOT NULL" in my and "BIGINT AUTO_INCREMENT PRIMARY KEY" in my
    assert "DATETIME(6)" in my and "FOREIGN KEY (`author_id`) REFERENCES `authors` (`id`) ON DELETE CASCADE" in my


def test_query_sql_is_inspectable():
    sql, params = Article.where(Article.views > 5, title__icontains="py").order("views").limit(3).sql()
    assert sql.startswith('SELECT "articles"."id"') and 'LOWER("articles"."title") LIKE LOWER(?)' in sql
    assert sql.endswith('ORDER BY "articles"."views" ASC LIMIT 3') and params == [5, "%py%"]


# --------------------------------------------------------------- replicas

def test_reads_go_to_the_replica_until_the_request_writes(tmp_path):
    from pyweb.db import connect
    primary = connect(f"sqlite:///{tmp_path / 'p.db'}")
    replica = connect(f"sqlite:///{tmp_path / 'r.db'}")
    for d in (primary, replica):
        d.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT)")
    replica.execute("INSERT INTO notes (text) VALUES ('from replica')")
    primary.replica = replica
    assert primary.execute("SELECT text FROM notes").fetchall() == [("from replica",)]
    with request_scope():
        primary.execute("INSERT INTO notes (text) VALUES ('written')")
        assert primary.execute("SELECT text FROM notes").fetchall() == [("written",)]   # read-your-writes
    with primary.transaction():
        assert primary.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1


def test_slow_queries_are_logged_with_a_plan(db, monkeypatch, caplog):
    from pyweb import db as dbmod
    monkeypatch.setattr(dbmod, "_SLOW_MS", 0.0)
    make(1)
    with caplog.at_level(logging.WARNING, logger="pyweb.db"):
        Article.where(Article.views > 0).count()
    assert "slow query" in caplog.text
