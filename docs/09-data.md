# Data and databases

Database code runs on the server: at the top of page functions and in
`@server` functions. PyWeb ships a dependency-free data layer that works
the same on SQLite, Postgres and MySQL. You can still use any Python
library (SQLAlchemy, an HTTP client, ...) if you prefer.

## Models

A Model is a database table written as a class:

```python
from datetime import datetime
from pyweb import App, Field, Model
from pyweb.models import Email, Text

app = App(database="sqlite:///app.db")   # or DATABASE_URL, or postgresql://..., mysql://...

class User(Model):
    email: Email = Field(unique=True)
    name: str = Field(max=80)

class Tag(Model):
    name: str = Field(unique=True, max=30)

class Post(Model):
    title: str = Field(min=3, max=120)
    body: Text = ""
    author: User                              # foreign key: column author_id
    tags: list[Tag] = []                      # many-to-many: join table posts_tags
    views: int = Field(0, min=0)
    published_at: datetime | None = None      # | None: the column may be empty
    created_at: datetime = Field(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
```

- Every Model gets an `id` primary key unless it declares one
  (`Field(primary_key=True)`).
- The table name is the plural of the class name (`posts`); set
  `class Meta: table = "..."` to choose another.
- Types: `int`, `str`, `float`, `bool`, `Decimal`, `datetime`, `date`,
  `time`, `bytes`, `uuid.UUID`, `dict`/`list` (stored as JSON), `Text`
  (long text), `Email`, `URL`, `Slug`. Datetimes are stored in UTC and
  come back timezone-aware.
- A field with no default and no `| None` is required.

### Field options

`Field(default, *, ...)` describes the column *and* its rules:

| Option | Meaning |
|---|---|
| `min`, `max` | bounds for numbers, length for text, size for lists |
| `pattern` | a regular expression the whole text must match |
| `choices` | allowed values |
| `format` | `"email"`, `"url"` or `"slug"` (implied by `Email`/`URL`/`Slug`) |
| `required`, `message` | override whether it's required, and the error message |
| `unique`, `index` | a unique index, or a plain one |
| `default_factory` | called for each new row (`default_factory=dict`) |
| `auto_now_add`, `auto_now` | set on insert / on every save |
| `private=True` | never accepted from browsers, left out of `to_dict()` (password hashes) |
| `readonly=True` | never accepted from browsers |
| `precision`, `scale` | for `Decimal` columns |

The same rules run when a row is saved, when an RPC argument arrives, when
a form is posted, and in the browser while the user types. Add your own
with `@validates`:

```python
from pyweb.models import validates

class User(Model):
    username: str = Field(max=30)

    @validates("username")
    def not_reserved(self, value):
        if value.lower() in ("admin", "root"):
            raise ValueError("is reserved")
```

Invalid rows raise `ValidationError`, whose `.errors` maps field names to
messages. A `ValidationError` raised in a `@server` function reaches the
browser as a 422 response with those errors, ready to show next to each
input. A unique index that is violated becomes `{"email": "is already taken"}`.

### Creating, reading, changing

```python
ada = User.create(email="ada@example.com", name="Ada")
post = Post.create(title="Hello", author=ada, tags=[python])

User.get(3)                     # by primary key, or None
User.get(email="ada@example.com")
Post.get_or_404(post_id)        # shows the 404 page if missing

post.title = "Hello again"
post.save()                     # writes only the columns that changed
post.update(views=10)           # set and save
post.delete()                   # rows pointing at it follow on_delete

user, created = User.get_or_create(email="a@b.co", defaults={"name": "A"})
Tag.upsert(key="name", name="python")
```

### Queries

```python
Post.where(Post.views > 100)
Post.where(author=ada, title__icontains="python")
Post.where((Post.views > 100) | Post.published_at.is_null())
Post.exclude(views=0).order("-views", "title").limit(10)
```

Queries are immutable (every method returns a new one) and lazy (nothing
runs until you use the results). Conditions combine with `&`, `|` and `~`.

| Keyword lookup | Expression form |
|---|---|
| `views=5`, `views__ne=5` | `Post.views == 5`, `Post.views != 5` |
| `views__gt`, `__gte`, `__lt`, `__lte` | `>`, `>=`, `<`, `<=` |
| `id__in=[1, 2]`, `id__not_in=[...]` | `Post.id.in_([1, 2])`, `.not_in(...)` |
| `title__contains`, `__icontains`, `__startswith`, `__endswith` | `.contains("x")`, ... (`%` and `_` are literal) |
| `published_at__isnull=True` | `.is_null()`, `.not_null()` |
| `views__between=(1, 9)` | `.between(1, 9)` |

Results: iterate, index (`q[0]`, `q[2:5]`), `len(q)`, `.all()`, `.first()`,
`.last()`, `.count()`, `.exists()`, `.values("id", "title")` (plain dicts),
`.pluck("id")`.

Totals: `Post.where(...).aggregate(n=Count(), total=Sum("views"))`;
per group: `Post.query().group_by("author").values("author", posts=Count())`.
`Count`, `Sum`, `Avg`, `Min`, `Max` come from `pyweb.models`.

Changing many rows at once: `Post.where(...).update(status="live")` and
`.delete()` (both refuse to touch every row unless you pass
`all_rows=True`).

**Pages of results.** `page()` paginates by cursor, which stays correct
while rows are added (offsets skip or repeat rows):

```python
page = Post.order("-created_at").page(size=20, after=request.query.get("after"))
for post in page: ...
page.next          # pass as `after` for the next page; None on the last page
```

Every value becomes a bound parameter and every column name is checked
against the Model, so input can never change the shape of the SQL.
`query.sql()` shows the SQL a query would run.

### Relations

```python
class Post(Model):
    author: User                                    # many posts per user
    editor: User | None = ForeignKey(related_name="edited", on_delete="set null")
    tags: list[Tag] = []                            # many-to-many

class Profile(Model):
    owner: User = OneToOne()                        # at most one per user
```

The other side is added for you: `user.posts`, `user.edited`,
`tag.posts`, `user.profile`. `on_delete` is `"cascade"` (the default),
`"set null"` (for `| None` relations) or `"restrict"`.

**Load related rows with `include()`**, one extra query per relation,
however many rows there are:

```python
posts = Post.where(Post.published_at.not_null()).include("author", "tags", "author.profile")
for post in posts:
    post.author.name, [t.name for t in post.tags]
```

Reading `post.author` without `include("author")` raises `NotIncluded`
while developing, naming the include to add, so a page can't quietly run
one query per row. In production (`PYWEB_ENV=production`) the row is
loaded on demand instead and a warning is logged. `post.author_id` (the
raw id) is always available.

Filtering by related rows uses subqueries, no joins needed:

```python
Post.where(Post.author.has(User.name == "Ada"))
User.where(User.posts.any(Post.views > 1000))
Post.where(Post.tags.any(Tag.name == "python"))
Post.where(Post.tags.none(Tag.name == "draft"))
```

Changing a relation writes straight away (no `include` needed):

```python
post.tags.add(python, web)
post.tags.remove(web)
post.tags.set([python])
post.tags.clear()
ada.posts.create(title="Written by Ada")
ada.posts.query().where(Post.views > 10).count()   # a normal query over related rows
```

### Transactions

Each `@server` call and form action is **one transaction**: it starts at
the first write and commits when the function returns, or rolls back if
it raises. Reads before the first write don't hold a connection, so a
function that calls a slow API doesn't keep the database busy.
`@atomic(False)` (from `pyweb`) opts a function out; `@atomic` puts any
other function in one transaction.

```python
with db.transaction():
    ...                      # committed at the end, rolled back on error
    with db.transaction():   # nested: a savepoint, can fail on its own
        ...
```

### Where rows live

First match wins: `Model.bind(db)` on a class; `App(database=...)`; the
`DATABASE_URL` variable. While developing, Models with none of these use
an in-memory SQLite database (data is lost on restart); in production
that is an error.

`DATABASE_REPLICA_URL` (or `connect(url, replica=...)`) sends reads made
outside transactions to a read replica. A request that has written reads
from the primary, so it always sees its own writes.

## Migrations

Migrations change the schema step by step, recorded in the
`pyweb_migrations` table.

```bash
pyweb db diff                 # compare Models with the database, write migrations/0001_....py
pyweb db upgrade              # apply pending migrations
pyweb db status               # what's applied, pending, and whether Models changed since
pyweb db downgrade --steps 1  # undo the latest
pyweb db seed                 # run seeds.py
```

`pyweb dev` applies pending migrations when it starts. In production run
`pyweb db upgrade` in your release step, or start servers with
`pyweb serve --migrate`: a lock shared by every server (a Postgres
advisory lock, MySQL `GET_LOCK`, or a row in `pyweb_locks`) means one
server migrates and the others wait, then find nothing to do.

Apps without a `migrations/` folder are in **prototype mode**: while
developing, tables are created as Models are first used. Run
`pyweb db diff` when you want real migrations.

A generated migration is ordinary Python you can edit:

```python
"""add column posts.summary"""

revision = '0002_add_column_posts_summary'
schema = '9c1e0f3b2a4d5e6f'
contract = False


def up(op):
    op.add_column('posts', op.column('summary', 'str', max_length=200))


def down(op):
    op.drop_column('posts', 'summary')
```

`op` offers `create_table`, `drop_table`, `rename_table`, `add_column`,
`drop_column`, `alter_column`, `rename_column`, `create_index`,
`drop_index`, `execute(sql, params)` and `run_python(fn)` (gets the
database, for data changes). On SQLite, changes SQLite can't `ALTER` are
done by rebuilding the table, with foreign keys paused so no `ON DELETE`
rule fires.

### Deploying without downtime: expand, then contract

While a new version rolls out, old and new code run against the same
database. So `pyweb db diff` splits changes in two:

- **Expand** (safe while old code runs): new tables, new columns (a
  required column is added as optional first), new indexes, and for a
  rename, a new column with the data copied in.
- **Contract** (removes things): dropping columns, making a column
  required, dropping the old copy of a renamed column. It goes in a
  separate `*_contract.py` file marked `contract = True`, which
  `pyweb db upgrade` leaves for later.

```bash
pyweb db diff --rename posts.title=headline   # say what's a rename, not a drop + add
pyweb db upgrade                               # expand, during the deploy
pyweb db upgrade --contract                    # once every server runs the new code
```

`--allow-destructive` puts everything in one migration, which is fine for
single-server apps. Tables in the database that no Model uses are reported,
never dropped automatically.

### Existing databases, squashing, plain SQL

- `pyweb db adopt` records an existing database as migration `0001`
  without changing it; later diffs build on it.
- `pyweb db squash` replaces many old migrations with one. Databases that
  applied the old ones record it without running it; new databases run
  only the squashed one.
- `pyweb db new --name x` writes a `NNN_x.up.sql` / `.down.sql` pair for
  hand-written SQL (as in 0.4); `--python` writes an empty Python
  migration. Both kinds can be mixed.

From Python: `pyweb.db.migrate.upgrade(db, "migrations")`, `downgrade`,
`status`, `make_migration(db, models, "migrations")`.

## Seeds and test data

```python
# seeds.py
from pyweb.db.seeds import seed

@seed
def admin():
    User.get_or_create(email="admin@example.com", defaults={"name": "Admin"})
```

`pyweb db seed` runs each seed in its own transaction. Write seeds so
running them twice is harmless.

For tests, `pyweb.testing.Factory` makes rows with defaults:

```python
from pyweb.testing import Factory

users = Factory(User, email=lambda n: f"user{n}@example.com", name="Test")
posts = Factory(Post, title=lambda n: f"Post {n}", author=users)
post = posts.create()                    # creates its author too
```

## Live queries

`Post.where(...).live()` (or `live(db, sql, params)`) in a page keeps a
page variable in step with the database: pages showing it update when its
tables are written. See [Live data](21-live-data.md).

## `pyweb.db`: SQL directly

```python
from pyweb.db import connect

db = connect("sqlite:///app.db")                 # relative path
# db = connect("sqlite:////var/data/app.db")     # absolute path
# db = connect("postgresql://user:pw@host/db")   # pip install "pyweb-stack[postgres]"
# db = connect("mysql://user:pw@host/db")        # pip install "pyweb-stack[mysql]"

db.execute("insert into posts (title) values (?)", ("Hello",))
rows = db.execute("select id, title from posts where id > ?", (0,)).dicts()
```

- **Parameters only.** Values are always passed separately from SQL.
- **Portable placeholders.** Write `?`; it is rewritten to `%s` for
  Postgres and MySQL drivers (and literal `%` is escaped).
- **Pooling.** Connections open lazily, up to `pool_size` (default 5),
  and are safe to share between request threads.
- **Results.** `.fetchall()`, `.fetchone()`, `.dicts()`, `.columns`,
  `.rowcount`, `.lastrowid`.
- **Streaming.** `db.stream(sql, params, chunksize=1000)` yields pages
  without loading everything.
- **Retries.** `db.execute(sql, params, attempts=3)` retries deadlocks,
  serialization failures and dropped connections.
- **SQLite** enforces foreign keys and uses write-ahead logging for files.
- **Slow queries** (over `PYWEB_SLOW_QUERY_MS`, default 200 ms while
  developing) are logged with the database's query plan.
- `pyweb.db.QUERY_HOOKS` is a list of functions called after every
  statement with `(db, sql, params, milliseconds, rowcount)`.

## Choosing SQLite, Postgres or MySQL

SQLite is ideal for single-server apps and tests. Use Postgres or MySQL
when several app servers share data. Models, queries and migrations are
tested against real SQLite, Postgres 16 and MySQL 8 in CI.
