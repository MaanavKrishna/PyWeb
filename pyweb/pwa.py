"""Installable apps: ``App(pwa=True)`` or ``App(pwa=PWA(...))``.

With it, PyWeb serves:

* ``/manifest.webmanifest``: name, colours, icons, start URL (what browsers need to offer "Install");
* ``/sw.js``: a service worker that keeps the app's static files for offline use, falls back
  to an offline page when a page can't load, and shows push notifications (:mod:`pyweb.push`);
* icons in the sizes browsers and phones ask for, made from one PNG (or a generated one);
* ``/__pyweb/pwa.js``: registers the worker (through a Trusted Types policy, so the strict
  CSP holds) and wires ``data-pw-push`` buttons.

What the service worker never keeps: server function calls, forms, live updates, anything
that isn't a GET, other sites' files, and pages of signed-in visitors (``cache_pages=True``
keeps only pages rendered for visitors who aren't signed in).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import struct
import zlib

from pyweb.runtime.server import Response

ICON_SIZES = (192, 512)
APPLE_ICON = 180
BROWSER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime", "browser")
PATHS = ("/manifest.webmanifest", "/sw.js", "/__pyweb/pwa.js", "/__pyweb/offline")


@dataclasses.dataclass
class PWA:
    """How the installed app looks and behaves. Every field is optional.

    ``icon``: a square PNG (512×512 or larger is best) or SVG, relative to the app's folder;
    without one PyWeb draws a plain icon in ``theme_color``. ``offline_page``: the path of one
    of your pages to show when there's no connection (default: a built-in page).
    ``cache_pages``: also keep pages visitors open while signed out, so they can read them
    offline. ``push``: allow Web Push notifications (see :mod:`pyweb.push`).
    """

    name: str | None = None
    short_name: str | None = None
    description: str | None = None
    icon: str | None = None
    theme_color: str = "#4f46e5"
    background_color: str = "#ffffff"
    display: str = "standalone"
    start_url: str = "/"
    scope: str = "/"
    orientation: str | None = None
    offline_page: str | None = None
    cache_pages: bool = False
    precache: tuple = ()
    shortcuts: tuple = ()
    categories: tuple = ()
    push: bool = True

    def __post_init__(self):
        if self.display not in ("standalone", "fullscreen", "minimal-ui", "browser"):
            raise ValueError(f"PWA display must be standalone, fullscreen, minimal-ui or browser, not {self.display!r}")
        for name in ("start_url", "scope"):
            value = getattr(self, name)
            if not value.startswith("/") or value.startswith("//"):
                raise ValueError(f"PWA {name} must be a path on this site, like '/'")
        if self.offline_page is not None and (not self.offline_page.startswith("/") or self.offline_page.startswith("//")):
            raise ValueError("PWA offline_page must be a path on this site, like '/offline'")
        for url in self.precache:
            if not str(url).startswith("/") or str(url).startswith("//"):
                raise ValueError(f"PWA precache entries must be paths on this site, not {url!r}")


def config(app) -> PWA | None:
    """The app's :class:`PWA` settings, or None when it isn't installable."""
    value = getattr(app, "pwa", None)
    if value is None or value is False:
        return None
    if value is True:
        return PWA()
    if isinstance(value, PWA):
        return value
    if isinstance(value, dict):
        return PWA(**value)
    raise TypeError("App(pwa=...) takes True or pyweb.pwa.PWA(...)")


# ------------------------------------------------------------------ PNG (stdlib only)

def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode_png(width, height, rgba: bytes) -> bytes:
    """An RGBA image as PNG bytes."""
    stride = width * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b""))


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode_png(data: bytes):
    """``(width, height, rgba)`` for an 8-bit, non-interlaced PNG (grey, grey+alpha, RGB, RGBA or palette)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos, idat, palette, trns = 8, [], None, None
    width = height = depth = ctype = interlace = None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if width is None:
        raise ValueError("PNG without a header")
    if depth != 8 or interlace:
        raise ValueError("icons must be 8-bit, non-interlaced PNGs (re-save the image to fix this)")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if channels is None:
        raise ValueError(f"unsupported PNG colour type {ctype}")
    if width * height > 4096 * 4096:
        raise ValueError("icon image is too large (at most 4096×4096)")
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    rows, prev = [], bytearray(stride)
    for y in range(height):
        off = y * (stride + 1)
        ftype, line = raw[off], bytearray(raw[off + 1:off + 1 + stride])
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if ftype == 1:
                line[i] = (line[i] + a) & 0xFF
            elif ftype == 2:
                line[i] = (line[i] + b) & 0xFF
            elif ftype == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                line[i] = (line[i] + _paeth(a, b, c)) & 0xFF
        rows.append(bytes(line))
        prev = line
    out = bytearray()
    for line in rows:
        for x in range(width):
            px = line[x * channels:(x + 1) * channels]
            if ctype == 6:
                out += px
            elif ctype == 2:
                out += px + b"\xff"
            elif ctype == 0:
                out += bytes((px[0], px[0], px[0], 255))
            elif ctype == 4:
                out += bytes((px[0], px[0], px[0], px[1]))
            else:
                i = px[0]
                rgb = palette[i * 3:i * 3 + 3] if palette and i * 3 + 3 <= len(palette) else b"\0\0\0"
                alpha = trns[i] if trns and i < len(trns) else 255
                out += rgb + bytes((alpha,))
    return width, height, bytes(out)


def resize(width, height, rgba: bytes, size: int) -> bytes:
    """``rgba`` scaled into a ``size``×``size`` square (centred, transparent margins; area-averaged)."""
    scale = size / max(width, height)
    new_w, new_h = max(1, round(width * scale)), max(1, round(height * scale))
    off_x, off_y = (size - new_w) // 2, (size - new_h) // 2
    out = bytearray(size * size * 4)
    for y in range(new_h):
        y0 = int(y * height / new_h)
        y1 = max(y0 + 1, int((y + 1) * height / new_h))
        for x in range(new_w):
            x0 = int(x * width / new_w)
            x1 = max(x0 + 1, int((x + 1) * width / new_w))
            r = g = b = a = n = 0
            for sy in range(y0, y1):
                base = sy * width * 4
                for sx in range(x0, x1):
                    i = base + sx * 4
                    pa = rgba[i + 3]
                    r += rgba[i] * pa
                    g += rgba[i + 1] * pa
                    b += rgba[i + 2] * pa
                    a += pa
                    n += 1
            o = ((y + off_y) * size + x + off_x) * 4
            if a:
                out[o:o + 4] = bytes((r // a, g // a, b // a, a // n))
    return bytes(out)


def _hex_rgb(color):
    c = (color or "").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (79, 70, 229)


def default_icon(size, color) -> bytes:
    """A plain icon: a rounded square in ``color`` with a light ring (no image needed)."""
    r, g, b = _hex_rgb(color)
    out = bytearray(size * size * 4)
    radius = size * 0.22
    c = (size - 1) / 2
    ring_outer, ring_inner = size * 0.28, size * 0.18
    for y in range(size):
        for x in range(size):
            dx = max(radius - x, 0, x - (size - 1 - radius))
            dy = max(radius - y, 0, y - (size - 1 - radius))
            if dx * dx + dy * dy > radius * radius:
                continue
            d2 = (x - c) ** 2 + (y - c) ** 2
            o = (y * size + x) * 4
            if ring_inner ** 2 <= d2 <= ring_outer ** 2:
                out[o:o + 4] = bytes((255, 255, 255, 255))
            else:
                out[o:o + 4] = bytes((r, g, b, 255))
    return encode_png(size, size, bytes(out))


_icon_cache: dict = {}


def icon_png(cfg: PWA, base_dir, size: int) -> bytes:
    """The app icon at ``size``×``size`` as PNG bytes (made once, then cached)."""
    path = os.path.join(base_dir or ".", cfg.icon) if cfg.icon and not cfg.icon.lower().endswith(".svg") else None
    stamp = os.path.getmtime(path) if path and os.path.exists(path) else None
    key = (path, stamp, cfg.theme_color, size)
    if key not in _icon_cache:
        if path:
            with open(path, "rb") as fh:
                w, h, rgba = decode_png(fh.read())
            _icon_cache[key] = encode_png(size, size, resize(w, h, rgba, size))
        else:
            _icon_cache[key] = default_icon(size, cfg.theme_color)
    return _icon_cache[key]


def _svg_icon(cfg, base_dir):
    if cfg.icon and cfg.icon.lower().endswith(".svg"):
        with open(os.path.join(base_dir or ".", cfg.icon), "rb") as fh:
            return fh.read()
    return None


# ------------------------------------------------------------------ documents

def manifest(cfg: PWA, *, title="PyWeb", description=None, lang="en") -> dict:
    """The web app manifest (a dict; served as JSON at ``/manifest.webmanifest``)."""
    name = cfg.name or title
    out = {
        "name": name,
        "short_name": cfg.short_name or (name if len(name) <= 12 else name[:12].rstrip()),
        "start_url": cfg.start_url,
        "scope": cfg.scope,
        "id": cfg.start_url,
        "display": cfg.display,
        "theme_color": cfg.theme_color,
        "background_color": cfg.background_color,
        "lang": lang,
        "icons": [{"src": f"/__pyweb/pwa/icon-{n}.png", "sizes": f"{n}x{n}", "type": "image/png", "purpose": "any"}
                  for n in ICON_SIZES],
    }
    if cfg.icon and cfg.icon.lower().endswith(".svg"):
        out["icons"].append({"src": "/__pyweb/pwa/icon.svg", "sizes": "any", "type": "image/svg+xml"})
    if cfg.description or description:
        out["description"] = cfg.description or description
    if cfg.orientation:
        out["orientation"] = cfg.orientation
    if cfg.categories:
        out["categories"] = list(cfg.categories)
    if cfg.shortcuts:
        out["shortcuts"] = [dict(s) for s in cfg.shortcuts]
    return out


def head_tags(cfg: PWA) -> str:
    """Tags every page gets: the manifest link, theme colour, iOS icon and the registration script."""
    from html import escape
    return ('<link rel="manifest" href="/manifest.webmanifest">'
            f'<meta name="theme-color" content="{escape(cfg.theme_color)}">'
            f'<link rel="apple-touch-icon" href="/__pyweb/pwa/icon-{APPLE_ICON}.png">'
            '<script type="module" src="/__pyweb/pwa.js"></script>')


def offline_html(cfg: PWA, *, title="PyWeb", lang="en") -> str:
    """The built-in page shown when a page can't load without a connection."""
    from html import escape
    name = escape(cfg.name or title)
    return ("<!doctype html>\n"
            f'<html lang="{escape(lang)}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>Offline · {name}</title>"
            f'<meta name="theme-color" content="{escape(cfg.theme_color)}">'
            "<style>body{font:16px/1.5 system-ui,sans-serif;margin:0;min-height:100vh;display:grid;"
            "place-items:center;background:#f8fafc;color:#0f172a}main{max-width:28rem;padding:2rem;text-align:center}"
            "h1{font-size:1.5rem;margin:0 0 .5rem}p{color:#475569}a{display:inline-block;margin-top:1rem;"
            "padding:.6rem 1.2rem;border-radius:.6rem;background:#0f172a;color:#fff;text-decoration:none}"
            "@media (prefers-color-scheme:dark){body{background:#0f172a;color:#f1f5f9}p{color:#94a3b8}"
            "a{background:#f1f5f9;color:#0f172a}}</style></head>"
            f"<body><main><h1>You're offline</h1><p>{name} can't reach the internet right now. "
            "Check your connection, then try again.</p>"
            '<a href="">Try again</a></main></body></html>\n')


def version(loaded, cfg: PWA) -> str:
    """Changes whenever the app or these settings change, so browsers fetch the new service worker."""
    from pyweb import __version__
    h = hashlib.sha256()
    h.update(__version__.encode())
    h.update((getattr(loaded, "source", "") or "").encode())
    h.update(json.dumps(getattr(loaded, "asset_urls", {}) or {}, sort_keys=True).encode())
    h.update(repr(dataclasses.astuple(cfg)).encode())
    return h.hexdigest()[:16]


def service_worker(cfg: PWA, ver: str, *, title="PyWeb") -> str:
    """``/sw.js``: the service worker source for this app."""
    offline = cfg.offline_page or "/__pyweb/offline"
    settings = {
        "version": ver,
        "offline": offline,
        "precache": list(dict.fromkeys([offline, f"/__pyweb/pwa/icon-{ICON_SIZES[0]}.png",
                                        *[str(u) for u in cfg.precache]])),
        "cachePages": bool(cfg.cache_pages),
        "name": cfg.name or title,
        "icon": f"/__pyweb/pwa/icon-{ICON_SIZES[0]}.png",
    }
    with open(os.path.join(BROWSER_DIR, "sw.js"), encoding="utf-8") as fh:
        source = fh.read()
    # </ can't end anything in a JS file, but keep the JSON inert all the same.
    return source.replace("__PYWEB_SW_CONFIG__", json.dumps(settings).replace("</", "<\\/"), 1)


# ------------------------------------------------------------------ serving

def handle(server, req):
    """Answer a PWA or push request (``/manifest.webmanifest``, ``/sw.js``...), or None."""
    path = req.path.split("?")[0]
    if not (path in PATHS or path.startswith(("/__pyweb/pwa/", "/__pyweb/push/"))):
        return None
    loaded = getattr(server, "app", None)
    cfg = config(getattr(loaded, "app", None))
    if cfg is None:
        return None
    if path.startswith("/__pyweb/push/"):
        from pyweb import push
        return push.handle(server, req, cfg)
    if req.method not in ("GET", "HEAD"):
        return Response(405, "method not allowed", {"Allow": "GET, HEAD", "Content-Type": "text/plain"})
    title = getattr(loaded, "title", None) or "PyWeb"
    lang = getattr(loaded, "lang", "en") or "en"
    base_dir = os.path.dirname(os.path.abspath(loaded.path)) if getattr(loaded, "path", None) else "."
    if path == "/manifest.webmanifest":
        app = getattr(loaded, "app", None)
        body = json.dumps(manifest(cfg, title=title, description=getattr(app, "description", None), lang=lang),
                          indent=1)
        return Response(200, body, {"Content-Type": "application/manifest+json", "Cache-Control": "no-cache"})
    if path == "/sw.js":
        # Browsers check for a new worker on every visit; no-cache makes them ask us each time.
        return Response(200, service_worker(cfg, version(loaded, cfg), title=title),
                        {"Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-cache",
                         "Service-Worker-Allowed": cfg.scope})
    if path == "/__pyweb/pwa.js":
        with open(os.path.join(BROWSER_DIR, "pwa.js"), encoding="utf-8") as fh:
            return Response(200, fh.read(), {"Content-Type": "text/javascript; charset=utf-8",
                                             "Cache-Control": "no-cache"})
    if path == "/__pyweb/offline":
        return Response(200, offline_html(cfg, title=title, lang=lang),
                        {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-cache"})
    if path == "/__pyweb/pwa/icon.svg":
        svg = _svg_icon(cfg, base_dir)
        if svg is None:
            return None
        return Response(200, svg, {"Content-Type": "image/svg+xml", "Cache-Control": "public, max-age=86400",
                                   "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})
    name = path[len("/__pyweb/pwa/"):]
    if name.startswith("icon-") and name.endswith(".png"):
        try:
            size = int(name[5:-4])
        except ValueError:
            return None
        if size not in (*ICON_SIZES, APPLE_ICON):
            return None
        return Response(200, icon_png(cfg, base_dir, size),
                        {"Content-Type": "image/png", "Cache-Control": "public, max-age=86400"})
    return None


def offline_header(server, req, headers):
    """Mark a page the service worker may keep offline (``cache_pages=True``): only pages rendered for
    visitors who aren't signed in, so a shared computer never shows someone's private page offline."""
    cfg = config(getattr(getattr(server, "app", None), "app", None))
    if cfg is None or not cfg.cache_pages:
        return
    from pyweb.auth import SESSION_COOKIE
    if SESSION_COOKIE in (getattr(req, "cookies", None) or {}):
        return
    if any(k.lower() == "authorization" for k in (req.headers or {})):
        return
    headers["X-PyWeb-Offline"] = "1"
