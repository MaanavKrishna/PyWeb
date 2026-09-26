# 01 - Tutorial: build the todo app

> Snippet sources are executed by `tests/test_e2e.py::TestDocSnippets`.

## The form + list

<!-- snippet: examples/snippets/todo_crud.py -->
```python
<form data-pw-id="todo-form" data-pw-action="add_todo">
  <input data-pw-id="todo-input" pw-bind="title" name="title">
  <button type="submit">Add</button>
</form>
<ul data-pw-id="todo-list" pw-bind="todos" pw-each="todo">
  <li data-pw-id="todo-1" pw-bind="todo.title">first todo</li>
</ul>
```

Full app: `examples/todo/app.pyweb`.

## The CRUD model

<!-- snippet: examples/snippets/todo_crud.py -->
```python
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
```

## How QA covers it

<!-- snippet: examples/snippets/e2e_usage.py -->
```python
from tests.e2e_harness import (
    assert_ssr_response, assert_hydration_markers, http_get, rpc_roundtrip,
)
res = http_get(base_url + "/")
assert_ssr_response(res, ["data-pw-id=\"counter-value\""])
assert_hydration_markers(res.body)
rpc_roundtrip(base_url, "increment", {"count": 1})
```

`add_todo` with an empty title must return `400 {"ok": false, ...}`
(`tests/test_e2e.py::TestTodoApp` asserts the success and the
validation-error path).
