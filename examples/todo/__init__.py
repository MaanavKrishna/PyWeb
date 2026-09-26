"""Todo example: Model CRUD end-to-end on SQLite (Track B)."""

from __future__ import annotations

from pyweb.app import PyWeb, escape
from pyweb.db import SQLiteDB
from pyweb.models import IntegerField, Model, TextField


class Todo(Model):
    __table__ = "todos"
    id = IntegerField(primary_key=True)
    title = TextField(nullable=False)
    done = IntegerField(default=0)


def make_app(db_path: str = ":memory:") -> PyWeb:
    db = SQLiteDB(db_path)
    db.execute(Todo.schema_sql())
    Todo.bind(db)
    app = PyWeb()

    @app.page("/", title="Todos")
    def index():
        items = Todo.all()
        rows = "".join(
            f"<li>{escape(t.title)} "
            f"{'[x]' if getattr(t, 'done', 0) else '[ ]'}</li>"
            for t in items
        )
        return f"<h1>Todos</h1><ul>{rows}</ul>"

    @app.rpc()
    def add_todo(title: str):
        return Todo.create(title=title).to_dict()

    @app.rpc()
    def toggle_todo(todo_id: int):
        todo = Todo.get(todo_id)
        if todo is None:
            raise ValueError("not found")
        todo.done = 0 if todo.done else 1
        todo.save()
        return todo.to_dict()

    @app.rpc()
    def list_todos():
        return [t.to_dict() for t in Todo.all()]

    app.todo_db = db  # type: ignore[attr-defined]
    return app


if __name__ == "__main__":
    make_app("todos.db").run(port=8000)
