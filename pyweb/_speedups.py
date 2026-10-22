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


# ----------------------------------------------------------------- JSON
#
# With the ``speed`` extra (``pip install "pyweb-stack[speed]"``), orjson encodes
# JSON about ten times faster: a 10,000-row live query goes from ~10 ms to ~1 ms.
# The output is compact JSON either way. Dates and dataclasses still go through
# ``default`` (as with the standard library), so the text is the same, except that
# tiny or huge floats are spelled ``1e-7`` instead of ``1e-07`` and NaN becomes
# ``null`` (browsers can't parse ``NaN``). A value orjson can't take (an int over
# 64 bits, say) falls back to the standard library.

import json as _json

try:
    import orjson as _orjson
except ImportError:          # the extra isn't installed: the standard library does it all
    _orjson = None

_SEP = (",", ":")
if _orjson is not None:
    _OPTS = _orjson.OPT_NON_STR_KEYS | _orjson.OPT_PASSTHROUGH_DATETIME | _orjson.OPT_PASSTHROUGH_DATACLASS
    _SORTED = _OPTS | _orjson.OPT_SORT_KEYS


def json_dumps(obj, *, sort_keys=False, default=None) -> str:
    """Compact JSON text for ``obj`` (orjson when installed)."""
    if _orjson is not None:
        try:
            return _orjson.dumps(obj, default=default, option=_SORTED if sort_keys else _OPTS).decode()
        except (TypeError, ValueError):
            pass
    return _json.dumps(obj, separators=_SEP, sort_keys=sort_keys, default=default, ensure_ascii=False)


def json_bytes(obj, *, sort_keys=False, default=None) -> bytes:
    """:func:`json_dumps` as UTF-8 bytes."""
    if _orjson is not None:
        try:
            return _orjson.dumps(obj, default=default, option=_SORTED if sort_keys else _OPTS)
        except (TypeError, ValueError):
            pass
    return json_dumps(obj, sort_keys=sort_keys, default=default).encode()


def fast_json() -> bool:
    """True when orjson is installed and used."""
    return _orjson is not None
