"""Resized copies of your images for ``<Image>`` (``from pyweb.ui import Image``).

``/__pyweb/img/<width>/<path>`` answers with ``static/<path>`` scaled down to ``width`` pixels
wide (never up), in the same format, without its metadata (EXIF: camera, location). Only the
widths in :data:`WIDTHS` are made, so a visitor can't make the server resize to every size.
Copies are kept in memory (up to 64 MB) and sent with the original's caching rules.

Needs Pillow (``pip install "pyweb-stack[images]"``); without it the original file is sent and
the dev server says so once.
"""

from __future__ import annotations

import io
import logging
import posixpath
import threading
from collections import OrderedDict

WIDTHS = (320, 640, 960, 1280, 1920, 2560)
RESIZABLE = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
MAX_PIXELS = 50_000_000            # bigger than this is left alone (decompression bombs)
CACHE_BYTES = 64 * 1024 * 1024

log = logging.getLogger("pyweb.images")
_cache: OrderedDict = OrderedDict()
_size = 0
_lock = threading.Lock()
_warned = False


def _pillow():
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    return Image, ImageOps


def resize(data, width, fmt):
    """``data`` scaled down to ``width`` (same format, no metadata); the original if it's not wider."""
    lib = _pillow()
    if lib is None:
        return data
    Image, ImageOps = lib
    with Image.open(io.BytesIO(data), formats=[fmt]) as im:     # only the format its name says
        if im.width * im.height > MAX_PIXELS or im.width <= width:
            return data
        im = ImageOps.exif_transpose(im)
        height = max(1, round(im.height * width / im.width))
        small = im.resize((width, height), Image.LANCZOS)
        out = io.BytesIO()
        if fmt == "JPEG":
            small.convert("RGB").save(out, "JPEG", quality=82, optimize=True, progressive=True)
        elif fmt == "WEBP":
            small.save(out, "WEBP", quality=82)
        else:
            small.save(out, "PNG", optimize=True)
        return out.getvalue() if out.tell() < len(data) else data


def _remember(key, body):
    global _size
    with _lock:
        if key in _cache:
            return
        _cache[key] = body
        _size += len(body)
        while _size > CACHE_BYTES and _cache:
            _, old = _cache.popitem(last=False)
            _size -= len(old)


def respond(static_dirs, rest, *, query="", if_none_match=None, dev=False):
    """``(status, headers, body)`` for ``/__pyweb/img/<rest>``."""
    global _warned
    from .hosting import static_file
    width_s, _, rel = rest.partition("/")
    if not width_s.isdigit() or int(width_s) not in WIDTHS or not rel:
        return 404, [("Content-Type", "text/plain")], b"not found"
    width = int(width_s)
    rel = posixpath.normpath(rel)             # one cache entry per file, whatever the spelling
    if rel.startswith(("../", "/")) or rel in ("..", "."):
        return 404, [("Content-Type", "text/plain")], b"not found"
    status, headers, body = static_file(static_dirs, rel, query=query, dev=dev)
    if status != 200:
        return status, headers, body
    names = {k.lower(): v for k, v in headers}
    fmt = RESIZABLE.get(names.get("content-type", ""))
    if fmt is None:
        return status, headers, body                           # SVG, GIF...: as they are
    if _pillow() is None:
        if dev and not _warned:
            _warned = True
            log.warning("<Image> sends full-size images: pip install Pillow to make smaller copies")
        return status, headers, body
    etag = names["etag"][:-1] + f'-{width}"'
    out_headers = [(k, v) for k, v in headers if k.lower() != "etag"] + [("ETag", etag)]
    if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
        return 304, out_headers, b""
    key = (rel, etag)
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
    if hit is None:
        try:
            hit = resize(body, width, fmt)
        except Exception as exc:  # noqa: BLE001 - a broken image is sent as it is
            log.warning("couldn't resize %s: %s", rel, exc)
            hit = body
        _remember(key, hit)
    return 200, out_headers, hit
