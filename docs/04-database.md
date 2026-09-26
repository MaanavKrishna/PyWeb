# 04 - Database

> Source: `examples/snippets/database.py` (executed against real sqlite3).

## Pattern

<!-- snippet: examples/snippets/database.py -->
```python
todos = db.table("todos").all()          # read path serves SSR
todo = db.table("todos").insert(title=title)  # write path serves RPC
```

## Tested sqlite sketch

<!-- snippet: examples/snippets/database.py -->
```python
def connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS todos"
        " (id TEXT PRIMARY KEY, title TEXT NOT NULL, done INTEGER NOT NULL)"
    )
    return conn


def insert_todo(conn: sqlite3.Connection, todo_id: str, title: str) -> dict:
    title = (title or "").strip()
    if not title:
        raise ValueError("validation: title required")
    conn.execute(
        "INSERT INTO todos (id, title, done) VALUES (?, ?, 0)", (todo_id, title)
    )
    conn.commit()
    return {"id": todo_id, "title": title, "done": False}


def all_todos(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT id, title, done FROM todos ORDER BY id").fetchall()
    return [{"id": r[0], "title": r[1], "done": bool(r[2])} for r in rows]
```

Guidance: reads serve SSR so pages render without JS; writes serve RPC
so `insert_todo`'s `ValueError` maps to the `400 {"ok": false}`
validation contract.
