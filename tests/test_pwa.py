"""Installable apps: manifest, service worker, icons, offline page, head tags (browser side:
tests/e2e/test_pwa_browser.py)."""

import json
import os
import struct
import zlib

import pytest

from pyweb import pwa
from pyweb.pwa import PWA
from pyweb.serve import DEFAULT_CSP
from pyweb.testing import TestClient

APP = """
from pyweb import App
from pyweb.pwa import PWA
app = App(title="Field Notes", description="Notes from the field", pwa={pwa})


@app.page("/")
def Home():
    <p>hi</p>


@app.page("/offline")
def Offline():
    <p>no network</p>
"""


def client(tmp_path, pwa_expr="True", **files):
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    path = tmp_path / "app.pyweb"
    path.write_text(APP.replace("{pwa}", pwa_expr))
    return TestClient(str(path))


def test_without_pwa_nothing_is_served(tmp_path):
    c = client(tmp_path, "None")
    assert c.get("/manifest.webmanifest").status == 404
    assert c.get("/sw.js").status == 404
    assert 'rel="manifest"' not in c.get("/").text


def test_manifest_and_head_tags(tmp_path):
    c = client(tmp_path)
    page = c.get("/").text
    assert '<link rel="manifest" href="/manifest.webmanifest">' in page
    assert '<meta name="theme-color" content="#4f46e5">' in page
    assert '<script type="module" src="/__pyweb/pwa.js"></script>' in page
    r = c.get("/manifest.webmanifest")
    assert r.status == 200 and r.header("Content-Type") == "application/manifest+json"
    m = r.json()
    assert m["name"] == "Field Notes" and m["short_name"] == "Field Notes"
    assert m["description"] == "Notes from the field"
    assert m["display"] == "standalone" and m["start_url"] == "/" and m["scope"] == "/"
    # What Chrome needs to offer "Install": 192px and 512px icons.
    assert {i["sizes"] for i in m["icons"]} == {"192x192", "512x512"}
    for icon in m["icons"]:
        got = c.get(icon["src"])
        assert got.status == 200 and got.header("Content-Type") == "image/png"
        w, h, _ = pwa.decode_png(got.body)
        assert f"{w}x{h}" == icon["sizes"]
    assert c.get("/__pyweb/pwa/icon-180.png").status == 200             # iOS home screen
    assert c.get("/__pyweb/pwa/icon-77.png").status == 404


def test_settings(tmp_path):
    c = client(tmp_path, 'PWA(name="Field Notes Pro", short_name="Notes", theme_color="#0f766e", '
                         'display="fullscreen", offline_page="/offline", shortcuts=[{"name": "New", "url": "/new"}])')
    m = c.get("/manifest.webmanifest").json()
    assert (m["name"], m["short_name"], m["theme_color"], m["display"]) == (
        "Field Notes Pro", "Notes", "#0f766e", "fullscreen")
    assert m["shortcuts"] == [{"name": "New", "url": "/new"}]
    assert "/offline" in c.get("/sw.js").text
    with pytest.raises(ValueError, match="display"):
        PWA(display="window")
    with pytest.raises(ValueError, match="start_url"):
        PWA(start_url="https://evil.example/")
    with pytest.raises(ValueError, match="offline_page"):
        PWA(offline_page="//evil.example")
    with pytest.raises(ValueError, match="precache"):
        PWA(precache=["https://cdn.example/x.js"])


def test_service_worker(tmp_path):
    c = client(tmp_path)
    r = c.get("/sw.js")
    assert r.status == 200 and r.header("Content-Type").startswith("text/javascript")
    assert r.header("Cache-Control") == "no-cache"                     # browsers look for updates each visit
    src = r.text
    assert "__PYWEB_SW_CONFIG__" not in src
    config = json.loads(src.split("const CONFIG = ", 1)[1].split(";\n", 1)[0])
    assert config["offline"] == "/__pyweb/offline" and "/__pyweb/offline" in config["precache"]
    assert config["cachePages"] is False and len(config["version"]) == 16
    # Never kept: anything but GET, other sites, PyWeb's endpoints (RPC, forms, live updates), credentials.
    for rule in ('request.method !== "GET"', "url.origin !== self.location.origin", 'url.pathname.startsWith("/__pyweb/")',
                 'request.headers.has("authorization")', "text/event-stream"):
        assert rule in src
    # A notification only ever opens a page of this app.
    assert "target.origin !== self.location.origin" in src
    off = c.get("/__pyweb/offline")
    assert off.status == 200 and "You're offline" in off.text and "<script" not in off.text


def test_service_worker_version_follows_the_app(tmp_path):
    c = client(tmp_path)
    v1 = c.get("/sw.js").text
    (tmp_path / "app.pyweb").write_text(APP.replace("{pwa}", "True").replace("<p>hi</p>", "<p>hello</p>"))
    c2 = TestClient(str(tmp_path / "app.pyweb"))
    assert c2.get("/sw.js").text != v1


def test_registration_uses_a_trusted_types_policy(tmp_path):
    assert "trusted-types pyweb-md pyweb-nav pyweb-sw" in DEFAULT_CSP
    src = open(os.path.join(pwa.BROWSER_DIR, "pwa.js"), encoding="utf-8").read()
    assert 'createPolicy("pyweb-sw"' in src
    calls = [line for line in src.splitlines() if "serviceWorker.register(" in line]
    assert calls and all("policy.createScriptURL(SW_URL)" in line for line in calls)
    assert "Notification.requestPermission()" in src and "addEventListener(\"click\"" in src
    js = client(tmp_path).get("/__pyweb/pwa.js")
    assert js.status == 200 and js.header("Content-Type").startswith("text/javascript")


def test_pages_are_only_marked_for_offline_when_signed_out(tmp_path):
    c = client(tmp_path)
    assert c.get("/").header("X-PyWeb-Offline") is None                # cache_pages is off by default
    c = client(tmp_path, "PWA(cache_pages=True)")
    assert c.get("/").header("X-PyWeb-Offline") == "1"
    c.cookies["pyweb_session"] = "anything"
    assert c.get("/").header("X-PyWeb-Offline") is None
    assert '"cachePages": true' in c.get("/sw.js").text


# ------------------------------------------------------------------ icons

def _png(width, height, ctype, rows, palette=None, trns=None):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + r for r in rows)
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, 0))
    if palette:
        out += chunk(b"PLTE", palette)
    if trns:
        out += chunk(b"tRNS", trns)
    return out + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def test_png_round_trip_and_colour_types():
    rgba = bytes([255, 0, 0, 255, 0, 255, 0, 128, 0, 0, 255, 0, 10, 20, 30, 255])
    assert pwa.decode_png(pwa.encode_png(2, 2, rgba)) == (2, 2, rgba)
    assert pwa.decode_png(_png(1, 1, 2, [b"\x01\x02\x03"]))[2] == b"\x01\x02\x03\xff"
    assert pwa.decode_png(_png(1, 1, 0, [b"\x07"]))[2] == b"\x07\x07\x07\xff"
    assert pwa.decode_png(_png(1, 1, 4, [b"\x07\x09"]))[2] == b"\x07\x07\x07\x09"
    assert pwa.decode_png(_png(2, 1, 3, [b"\x00\x01"], palette=b"\x01\x02\x03\x04\x05\x06", trns=b"\x00"))[2] == \
        b"\x01\x02\x03\x00\x04\x05\x06\xff"
    with pytest.raises(ValueError, match="not a PNG"):
        pwa.decode_png(b"GIF89a")


def test_png_filters_are_undone():
    # Sub, Up, Average and Paeth filtered rows decode to the same pixels.
    rows = [bytes([10, 20, 30]), bytes([40, 50, 60])]
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    head = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 2, 8, 2, 0, 0, 0))
    up = b"\x00" + rows[0] + b"\x02" + bytes((b - a) & 255 for a, b in zip(rows[0], rows[1]))
    avg = b"\x00" + rows[0] + b"\x03" + bytes((b - (a >> 1)) & 255 for a, b in zip(rows[0], rows[1]))
    paeth = b"\x00" + rows[0] + b"\x04" + bytes((b - a) & 255 for a, b in zip(rows[0], rows[1]))
    for raw in (up, avg, paeth):
        png = head + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
        assert pwa.decode_png(png)[2] == b"\x0a\x14\x1e\xff\x28\x32\x3c\xff"


def test_icons_from_the_apps_png(tmp_path):
    # A 4x2 image: left half red, right half blue; scaled into a square it's centred.
    rgba = (bytes([255, 0, 0, 255]) * 2 + bytes([0, 0, 255, 255]) * 2) * 2
    c = client(tmp_path, 'PWA(icon="logo.png")', **{"logo.png": pwa.encode_png(4, 2, rgba)})
    w, h, px = pwa.decode_png(c.get("/__pyweb/pwa/icon-192.png").body)
    assert (w, h) == (192, 192)

    def at(x, y):
        o = (y * w + x) * 4
        return tuple(px[o:o + 4])
    assert at(10, 96) == (255, 0, 0, 255) and at(180, 96) == (0, 0, 255, 255)
    assert at(96, 5)[3] == 0                                          # transparent margin above


def test_svg_icon(tmp_path):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'
    c = client(tmp_path, 'PWA(icon="logo.svg")', **{"logo.svg": svg})
    m = c.get("/manifest.webmanifest").json()
    assert {"src": "/__pyweb/pwa/icon.svg", "sizes": "any", "type": "image/svg+xml"} in m["icons"]
    r = c.get("/__pyweb/pwa/icon.svg")
    assert r.body == svg and "default-src 'none'" in r.header("Content-Security-Policy")
    assert c.get("/__pyweb/pwa/icon-192.png").status == 200            # PNGs still drawn for the manifest
