"""PyWeb's UI kit: themed, accessible components and motion.

In a ``.pyweb`` file::

    from pyweb.ui import Button, Card, CardHeader, CardTitle, CardContent

    <Card>
        <CardHeader><CardTitle>Billing</CardTitle></CardHeader>
        <CardContent><Button variant="outline">Change plan</Button></CardContent>
    </Card>

Each component is a ``.pyweb`` file in ``pyweb/ui/kit/`` (``pyweb ui add button`` copies
it into your app to change it freely). ``App(theme=Theme(accent=...))`` sets the look.
"""

from __future__ import annotations

from .theme import PRESETS, Theme

__all__ = ["Theme", "PRESETS", "toast"]

TONES = ("success", "danger", "warning", "info")


def toast(title, description=None, *, tone=None, duration=5000, action=None):
    """Show a toast on the page that made this request (from a server function, a form action or a
    page). ``tone``: success, danger, warning or info. ``duration`` in ms (0: until dismissed).
    ``action``: ``{"label": "Undo", "href": "/undo/42"}``. Needs a page that uses the UI kit."""
    from pyweb import context
    if tone is not None and tone not in TONES:
        raise ValueError(f"toast tone is one of {', '.join(TONES)}")
    item = {"title": str(title)[:200]}
    if description:
        item["description"] = str(description)[:500]
    if tone:
        item["tone"] = tone
    if duration != 5000:
        item["duration"] = int(duration)
    if action:
        href = str(action.get("href") or "")
        if href and not (href.startswith("/") and not href.startswith("//")) and not href.startswith(("https://", "http://")):
            raise ValueError("toast action href must be a path or an http(s) URL")
        item["action"] = {"label": str(action.get("label", ""))[:40], **({"href": href} if href else {})}
    context.current().toasts.append(item)
    return item


class KitComponent:
    """What ``from pyweb.ui import Button`` gives Python code: the component is markup, rendered
    by the page that uses it (``<Button>``), not called."""

    def __init__(self, name, path):
        self.__name__, self.path = name, path

    def __call__(self, *a, **k):
        raise TypeError(f"{self.__name__} is a UI component: use it in markup, <{self.__name__}>...</{self.__name__}>")

    def __repr__(self):
        return f"<pyweb.ui component {self.__name__}>"


def components():
    """``{name: path}`` of every component in the kit."""
    from pyweb.compiler.pipeline import kit_index
    return dict(kit_index())


def __getattr__(name):
    index = components()
    if name in index:
        return KitComponent(name, index[name])
    raise AttributeError(f"module 'pyweb.ui' has no attribute {name!r}")
