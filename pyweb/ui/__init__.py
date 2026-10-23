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

__all__ = ["Theme", "PRESETS"]


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
