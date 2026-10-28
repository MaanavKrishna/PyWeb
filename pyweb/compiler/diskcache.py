"""An on-disk cache of parse results and module code, so a fresh ``pyweb dev`` (or test run) starts fast.

On only when ``PYWEB_CACHE=1`` (``pyweb dev`` and the test suite set it; ``PYWEB_CACHE=0`` turns it
off). Entries are keyed by the source's hash plus the PyWeb and Python versions, written atomically,
and pruned to the newest few hundred. A missing, corrupt or unreadable entry is ignored (and rewritten).

The cache lives in ``$PYWEB_CACHE_DIR``, else ``$XDG_CACHE_HOME/pyweb``, else ``~/.cache/pyweb``: a
folder only you can write to, holding what your own source files compile to. Entries hold code that
runs, so a folder or file that isn't yours, or that others can write to, is never read.
"""

import hashlib
import os
import sys
import tempfile

KEEP = 300


def enabled():
    return os.environ.get("PYWEB_CACHE") == "1"


def _dir():
    from .. import __version__
    base = os.environ.get("PYWEB_CACHE_DIR") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"), "pyweb")
    return os.path.join(base, f"{__version__}-{sys.implementation.cache_tag}")


def key(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode() if isinstance(p, str) else p)
        h.update(b"\0")
    return h.hexdigest()


def _private(st):
    """Owned by this user and not writable by anyone else (always true where there are no uids)."""
    if not hasattr(os, "getuid"):
        return True
    return st.st_uid == os.getuid() and not st.st_mode & 0o022


def get(kind, k):
    if not enabled():
        return None
    try:
        folder = _dir()
        if not _private(os.stat(folder)):
            return None
        with open(os.path.join(folder, f"{kind}-{k}.bin"), "rb") as fh:
            if not _private(os.fstat(fh.fileno())):
                return None
            return fh.read()
    except OSError:
        return None


def put(kind, k, data):
    if not enabled():
        return
    try:
        folder = _dir()
        os.makedirs(folder, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, os.path.join(folder, f"{kind}-{k}.bin"))
        _prune(folder)
    except OSError:
        pass                                   # read-only home, full disk: just don't cache


def _prune(folder):
    try:
        names = [n for n in os.listdir(folder) if n.endswith(".bin")]
        if len(names) <= KEEP:
            return
        paths = sorted((os.path.join(folder, n) for n in names), key=os.path.getmtime)
        for path in paths[: len(paths) - KEEP]:
            os.remove(path)
    except OSError:
        pass
