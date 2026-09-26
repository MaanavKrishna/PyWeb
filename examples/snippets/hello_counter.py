"""Snippet: hello-world counter app source (docs/00-quickstart.md)."""

SOURCE = """<main data-pw-id="counter-app">
  <h1 data-pw-id="counter-title">Counter</h1>
  <div data-pw-id="counter-value" pw-bind="count">0</div>
  <button data-pw-id="counter-inc" data-pw-action="increment">+</button>
</main>
"""


def render(count: int = 0) -> str:
    """Render the snippet with a count (mirrors SSR output)."""
    return (
        "<!DOCTYPE html><html><body>"
        f'<div data-pw-id="counter-value" pw-bind="count">{count}</div>'
        "</body></html>"
    )


if __name__ == "__main__":
    print(render(0))
