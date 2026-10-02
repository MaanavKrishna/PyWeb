# Data and databases

Database code runs on the server: at the top of page functions and in
`@server` functions. You can use any Python library (SQLAlchemy,
Django ORM, an HTTP client). PyWeb also ships a small, dependency-free
database layer.

## `pyweb.db`

```python
from pyweb.db import connect

db = connect("sqlite:///app.db")                 # relative path
# db = connect("sqlite:////var/data/app.db")     # absolute path
# db = connect("postgresql://user:pw@host/db")   # pip install "pyweb[postgres]"
# db = connect("mysql://user:pw@host/db")        # pip install "pyweb[mysql]"

db.execute("insert into posts (title) values (?)", ("Hello",))
rows = db.execute("select id, title from posts where id > ?", (0,)).dicts()
first = db.execute("select count(*) from posts").fetchone()
```

- **Parameters only.** Values are always passed separately from SQL.
- **Portable placeholders.** Write `?`; it is rewritten to `%s` for
  Postgres and MySQL drivers (and literal `%` is escaped). SQL that
  already uses `%s` is passed through unchanged.
- **Pooling.** Connections open lazily, up to `pool_size` (default 5),
  and are safe to share between request threads.
- **Results.** `.fetchall()`, `.fetchone()`, `.dicts()`, `.columns`,
  `.rowcount`, `.lastrowid`.

### Transactions

```python
with db.transaction():
    db.execute("update accounts set balance = balance - ? where id = ?", (10, 1))
    db.execute("update accounts set balance = balance + ? where id = ?", (10, 2))
# committed here; any exception rolls both back
```

### Streaming large results

```python
for page in db.stream("select * from events where day = ?", ("2026-01-01",), chunksize=1000):
    handle(page.dicts())
```

### Retries

`db.execute(sql, params, attempts=3)` retries transient failures
(deadlocks, serialization failures, dropped connections) and raises
`TransientDBError` if they persist.

## Migrations

```bash
pyweb db new --name create_posts --migrations migrations   # writes 001_create_posts.up.sql / .down.sql
pyweb db migrate --database "$DATABASE_URL"              # applies pending, records them
pyweb db status --database "$DATABASE_URL"
pyweb db rollback --database "$DATABASE_URL" --steps 1   # runs .down.sql files
```

Applied versions are recorded in a `pyweb_migrations` table. Applying is
idempotent; a `.down.sql` without its `.up.sql` is rejected.

From Python: `pyweb.db.migrate.migrate(db_or_url, "migrations")`,
`status(...)`, `rollback(..., steps=1)`.

## Choosing SQLite, Postgres or MySQL

SQLite is ideal for single-server apps and tests. Use Postgres or MySQL
when several app servers share data. The drivers are tested against
real Postgres 16, MySQL 8.4 and SQLite in CI. DDL syntax differs
between engines (for example auto-increment keys), so keep schema in
migrations per engine if you need to support more than one.

## Query builder

`pyweb.db.Query` builds parameterised SQL with validated identifiers:

```python
from pyweb.db import Query

sql, params = Query("posts").select("id", "title").where(author="ada").order_by("-id").limit(10).build_select()
```
