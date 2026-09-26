"""Browser APIs, CSS pipeline, production build."""

import asyncio
import json
import os

import pytest

import importlib as _il
# NOTE: ``pyweb.browser`` the module shares its name with the ``@browser``
# decorator (``from pyweb import browser``). importlib resolves via
# sys.modules so this is immune to the package-attr shadowing.
_b = _il.import_module("pyweb.browser")
from pyweb import build as _build
from pyweb import css as _css


def test_storage_server_stub_roundtrip():
    _b.storage["theme"] = "dark"
    assert _b.storage["theme"] == "dark"
    assert _b.storage.get("missing", "dflt") == "dflt"
    del _b.storage["theme"]
    assert _b.storage["theme"] is None


def test_sensor_apis_raise_on_server():
    for coro in (_b.clipboard.write("x"), _b.location.current(),
                 _b.camera.photo(), _b.notifications.send("t"),
                 _b.share.text("t", "x"), _b.filesystem.pick_text()):
        with pytest.raises(_b.BrowserUnavailable):
            asyncio.run(coro)
    with pytest.raises(_b.BrowserUnavailable):
        _b.location.watch(lambda p: None)


def test_graceful_defaults_on_server():
    assert asyncio.run(_b.fetch.get("https://x"))["status"] == 0
    assert asyncio.run(_b.permissions.query("camera")) == "denied"
    assert _b.vibrate.pulse() is False


def test_idb_server_falls_back_to_memory():
    async def go():
        await _b.idb.set("k", "v")
        assert await _b.idb.get("k") == "v"
        await _b.idb.delete("k")
        assert await _b.idb.get("k", "d") == "d"
    asyncio.run(go())


def test_bindings_cover_all_apis():
    names = _build.bindings() if hasattr(_build, "bindings") else _b.bindings()
    for expect in ("storage", "clipboard", "location", "notifications",
                   "camera", "fetch", "idb", "bluetooth", "filesystem"):
        assert expect in names, expect


def test_css_dict_px_and_unitless():
    cls, sheet = _css.css({"padding": 12, "border_radius": 8,
                           "opacity": 0.5, "z_index": 3})
    assert "padding:12px" in sheet and "border-radius:8px" in sheet
    assert "opacity:0.5" in sheet and "z-index:3" in sheet
    assert sheet.startswith(f".{cls}")


def test_tokens_vars_and_stylesheet():
    t = _css.Tokens(primary="#635bff", space_md="16px")
    assert t.var("primary") == "var(--primary)"
    assert t.primary == "#635bff"
    sheet = t.stylesheet()
    assert "--primary:#635bff" in sheet
    with pytest.raises(KeyError):
        t.var("nope")
    with pytest.raises(AttributeError):
        t.nope


def test_css_module_scoping_is_stable():
    src = ".btn { color: red; } .btn:hover { color: blue; } h1 { margin: 0; }"
    m1, scoped1 = _css.module(src)
    m2, scoped2 = _css.module(src)
    assert m1 == m2 and scoped1 == scoped2  # deterministic
    assert "h1" in scoped1 and ".btn_" in scoped1
    assert ".btn " not in scoped1  # original selector gone


def test_tailwind_markers():
    assert _css.is_tailwind(_css.tw("flex gap-4"))
    assert not _css.is_tailwind("flex gap-4")


def test_extract_styles_splits_blocks():
    html = "<head><style>.a{color:red}</style></head><body>x</body>"
    clean, css_text = _css.extract_styles(html)
    assert "<style>" not in clean and ".a{color:red}" in css_text


def test_minify_shrinks_and_keeps_semantics():
    js = "/* c */\nfunction f( a , b ) {\n  // add\n  return a + b;\n}\n"
    out = _build.minify_js(js)
    assert len(out) < len(js) and "return a+b;" in out


def test_build_writes_hashed_split_artifacts(tmp_path):
    compiled = {"pages": {"home": {
        "route": "/", "js": "function home(){ return 1; }\n",
        "html": ("<html><head><style>.a{color:red}</style></head>"
                 '<body><script src="runtime.js"></script>'
                 '<script src="home.js"></script></body></html>'),
        "signals": ["count"], "computeds": [], "sourcemap": []}},
        "rpc": [{"name": "get_count"}], "ir_text": "Home"}
    manifest = _build.build(compiled, str(tmp_path))
    page = manifest["pages"]["home"]
    assert page["js"].startswith("home.") and page["js"].endswith(".js")
    assert page["css"].startswith("home.") and "runtime." in page["runtime"]
    assert os.path.exists(str(tmp_path / "static" / page["js"]))
    assert os.path.exists(str(tmp_path / "static" / page["css"]))
    assert os.path.exists(str(tmp_path / "deploy" / "Dockerfile"))
    html = open(str(tmp_path / "server" / "home.html")).read()
    assert page["js"] in html and page["runtime"] in html
    assert "<style>" not in html  # extracted to file


def test_content_hash_stable():
    assert _build.content_hash("x") == _build.content_hash("x")
    assert _build.content_hash("x") != _build.content_hash("y")
