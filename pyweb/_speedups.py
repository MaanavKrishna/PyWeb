"""Hot paths behind one interface.

Everything here is plain Python today. Profiling decided what lives here: if a
future version moves one of these to native code, it keeps exactly these names
and behaviour, and nothing else in PyWeb changes. (See docs/29-performance.md
for the measurements and why PyWeb has no native core.)
"""

from __future__ import annotations

import os
import random

# ------------------------------------------------------------------ ids

_ids = random.Random(os.urandom(32))


def _reseed():
    _ids.seed(os.urandom(32))


if hasattr(os, "register_at_fork"):      # worker processes must never share a sequence
    os.register_at_fork(after_in_child=_reseed)


def trace_id() -> str:
    """A new W3C trace id: 32 lowercase hex digits, never all zero.

    Request and trace ids are labels for logs and traces, not secrets, so they
    come from a seeded generator instead of ``os.urandom``: a system call per id
    releases the GIL, and under load the request threads then queue to get it
    back (measured: four such calls per request cost about 30% of throughput).
    Anything secret (session ids, tokens, keys) still uses :mod:`secrets`.
    """
    return f"{_ids.getrandbits(128) or 1:032x}"


def span_id() -> str:
    """A new W3C span id: 16 lowercase hex digits, never all zero."""
    return f"{_ids.getrandbits(64) or 1:016x}"
