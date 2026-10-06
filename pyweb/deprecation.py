"""Deprecations: old APIs keep working for a release and say what replaces them.

``PYWEB_STRICT_DEPRECATIONS=1`` turns each warning into an error, so CI finds the
last uses of old APIs before they're removed. ``pyweb upgrade --check`` lists them
from the source without running anything.
"""

from __future__ import annotations

import os
import warnings


class PyWebDeprecationWarning(DeprecationWarning):
    """A PyWeb API that still works but will be removed."""


class PyWebDeprecationError(RuntimeError):
    """A deprecated PyWeb API was used while ``PYWEB_STRICT_DEPRECATIONS`` is set."""


# Shown by default (Python hides DeprecationWarning outside __main__): these are about the app's code.
warnings.filterwarnings("default", category=PyWebDeprecationWarning)


def strict():
    return os.environ.get("PYWEB_STRICT_DEPRECATIONS", "").strip().lower() in ("1", "true", "yes", "on")


def deprecated(message, *, stacklevel=3):
    """Warn that something is deprecated (``message`` says what to use instead), or raise when strict."""
    if strict():
        raise PyWebDeprecationError(message + " (PYWEB_STRICT_DEPRECATIONS is set)")
    warnings.warn(message, PyWebDeprecationWarning, stacklevel=stacklevel)
