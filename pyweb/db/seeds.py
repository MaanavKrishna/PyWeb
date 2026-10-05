"""Seed data: ``seeds.py`` next to the app, run with ``pyweb db seed``.

::

    from pyweb.db.seeds import seed

    @seed
    def admin_user():
        User.get_or_create(email="admin@example.com", defaults={"name": "Admin"})

Seeds run in the order they are defined, each in its own transaction. Write
them so running twice is harmless (``get_or_create``, ``upsert``).
"""

from __future__ import annotations

import os

_SEEDS: list = []


def seed(fn):
    """Register a seed function."""
    _SEEDS.append(fn)
    return fn


def run(path="seeds.py", *, db=None, only=None):
    """Run the seeds in ``path`` (a file). Returns the names that ran."""
    from pyweb import models as M
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no seeds file at {path}")
    start = len(_SEEDS)
    ns = {"__file__": os.path.abspath(path), "__name__": "pyweb_seeds"}
    with open(path, encoding="utf-8") as fh:
        exec(compile(fh.read(), path, "exec"), ns)  # noqa: S102 - the app's own seeds
    fns = _SEEDS[start:]
    del _SEEDS[start:]
    db = db or M.database()
    ran = []
    for fn in fns:
        if only and fn.__name__ not in only:
            continue
        if db is not None:
            with db.transaction():
                fn()
        else:
            fn()
        ran.append(fn.__name__)
    return ran
