"""Snippet: reactivity primitives used by docs/02-reactivity.md."""

SOURCE = """<div data-pw-id="counter-value" pw-bind="count">0</div>
<ul data-pw-id="todo-list" pw-bind="todos" pw-each="todo">
  <li data-pw-id="todo-1">first todo</li>
</ul>
"""


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


if __name__ == "__main__":
    seen: list = []
    count = Signal(0)
    count.subscribe(seen.append)
    count.value = 1
    print(render_count(count.value))
    print(seen)
