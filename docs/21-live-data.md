# Live data

A page usually shows a snapshot of the database from when it rendered.
With `live()`, a page variable follows the database instead: when a
table it reads changes, every open page showing it gets the new rows. No
polling, channels or handlers to write.

```pyweb
from pyweb import App, live, server
from pyweb.db import connect

app = App(title="Orders")
db = connect("sqlite:///shop.db")
db.execute("create table if not exists orders (id integer primary key, item text, status text default 'new')")


@server
def place(item: str) -> None:
    db.execute("insert into orders (item) values (?)", (item,))


@server
def ship(order_id: int) -> None:
    db.execute("update orders set status = 'shipped' where id = ?", (order_id,))


@app.page("/")
def Orders():
    orders = live(db, "select id, item, status from orders order by id desc limit 50")
    waiting = live(db, "select count(*) as n from orders where status = 'new'")
    item = ""

    def order():
        place(item)
        item = ""

    <h1>Orders ({waiting[0]["n"]} waiting)</h1>
    <form onsubmit={order}><input bind={item} /><button>Order</button></form>
    <ul>
        for o in orders:
            <li>{o["item"]}: {o["status"]}
                if o["status"] == "new":
                    <button onclick={lambda: ship(o["id"])}>Ship</button>
            </li>
    </ul>
```

Open the page in two windows: an order placed or shipped in one appears
in both at once.

## How it works

- **`live(db, sql, params)`** runs the query while the page renders, like
  `db.execute(sql, params).dicts()`, so the first paint has the data and
  needs no extra request. In the browser the variable is reactive state.
- **Writes announce themselves.** Every `INSERT`, `UPDATE`, `DELETE`,
  `REPLACE` or `TRUNCATE` made through `pyweb.db` (and so through
  `pyweb.models`) announces its table once it's committed. Inside
  `with db.transaction():` the announcements wait for the commit, and a
  rollback sends none.
- **Queries are shared.** Each distinct query (same database, SQL and
  parameters) is re-run once per change, however many pages show it,
  and only sends rows when they actually changed. A burst of writes is
  coalesced into one re-run.
- **Changes travel as patches.** When rows have an `id` column (or you
  pass `key="column"`), a change sends only the rows that were added,
  changed or removed, and where rows moved. Editing one row of a 500-row
  list sends that one row, and the browser redraws only it: the other
  rows keep their DOM (and focus, selection, animations). A page that
  missed a change (it was offline) gets the whole result once.
- **Pages follow a signed feed**, like `channel()`: only visitors who
  were served the page can listen, and nothing written after the render
  is missed.

With Models, `.live()` on a query does the same:

```python
orders = Order.where(status="new").order("-id").limit(50).live()
```

The tables to watch are the ones after `FROM` and `JOIN`. Pass
`tables=["orders", "customers"]` when that isn't enough (views,
functions, subqueries).

## Writes PyWeb doesn't see

If another program, a cron job or a raw driver connection writes to the
database, tell the live queries:

```python
db.notify("orders")            # after the other write has committed
```

## Several processes and servers

With more than one worker process or server, set `PYWEB_REDIS_URL`: the
realtime bus then goes through Redis, and a write in any process reaches
pages connected to any other. A process that didn't render a page (the
browser's connection landed on another worker, or the worker restarted)
takes over re-running its query from the page's signed query description.

## Limits

- Live queries are for what a page shows: keep them small with `LIMIT`.
  A live query that returns more than 10,000 rows is an error.
- Rows without a key (no `id` column and no `key=`) are sent whole on
  every change. For large lists, give rows a key, page them, or show counts.
- Rows are sent to every page showing the query, so filter per user in
  SQL (`where owner = ?`, with the id from `session.user()`), never in the
  browser.
- A query nobody has rendered or watched for ten minutes is forgotten
  until a page renders it again.
