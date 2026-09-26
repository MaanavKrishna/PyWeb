# 02 - Reactivity

> Source: `examples/snippets/reactivity.py` (executed by the suite).

## Binds

<!-- snippet: examples/snippets/reactivity.py -->
```python
<div data-pw-id="counter-value" pw-bind="count">0</div>
<ul data-pw-id="todo-list" pw-bind="todos" pw-each="todo">
  <li data-pw-id="todo-1">first todo</li>
</ul>
```

`pw-bind` names the reactive value; `data-pw-id` names the stable node
the client hydrates. Both markers must appear in SSR HTML
(`assert_hydration_markers`).

## Signals

<!-- snippet: examples/snippets/reactivity.py -->
```python
class Signal:
    """Minimal signal mirroring the reactive primitive docs describe."""

    def __init__(self, value):
        self._value = value
        self._subs: list = []

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, next_value):
        self._value = next_value
        for sub in list(self._subs):
            sub(next_value)

    def subscribe(self, fn):
        self._subs.append(fn)
        return fn


def render_count(count: int) -> str:
    return f'<div data-pw-id="counter-value" pw-bind="count">{count}</div>'
```

Rules:

- one `Signal` per bind; SSR renders its current value;
- setting the value re-renders subscribers in subscription order;
- lists use `pw-each` over the bound collection (see the todo app).
