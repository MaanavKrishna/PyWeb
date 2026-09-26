"""Snippet: todo app source used by docs/01-tutorial-todo.md."""

SOURCE = """<form data-pw-id="todo-form" data-pw-action="add_todo">
  <input data-pw-id="todo-input" pw-bind="title" name="title">
  <button type="submit">Add</button>
</form>
<ul data-pw-id="todo-list" pw-bind="todos" pw-each="todo">
  <li data-pw-id="todo-1" pw-bind="todo.title">first todo</li>
</ul>
"""

# In-memory model mirroring the CRUD the e2e suite exercises via RPC.
TODOS: list[dict] = [{"id": "todo-1", "title": "first todo", "done": False}]


def add_todo(title: str) -> dict:
    """Add a todo; raises ValueError on validation failure (mirrors RPC 400)."""
    title = (title or "").strip()
    if not title:
        raise ValueError("validation: title required")
    todo = {"id": f"todo-{len(TODOS) + 1}", "title": title, "done": False}
    TODOS.append(todo)
    return todo


def toggle_todo(todo_id: str) -> dict:
    for todo in TODOS:
        if todo["id"] == todo_id:
            todo["done"] = not todo["done"]
            return todo
    raise KeyError(todo_id)


def delete_todo(todo_id: str) -> None:
    remaining = [t for t in TODOS if t["id"] != todo_id]
    if len(remaining) == len(TODOS):
        raise KeyError(todo_id)
    TODOS[:] = remaining


if __name__ == "__main__":
    print(add_todo("write docs"))
    print(toggle_todo("todo-1"))
