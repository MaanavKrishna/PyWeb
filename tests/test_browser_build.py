"""Browser APIs, CSS pipeline, production build."""

import asyncio
import os

import pytest

import importlib as _il
# NOTE: ``pyweb.browser`` the module shares its name with the ``@browser``
# decorator (``from pyweb import browser``). importlib resolves via
# sys.modules so this is immune to the package-attr shadowing.
# Users should prefer ``from pyweb.browser import storage, ...``.
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


def test_user_import_patterns():
    """`pyweb.browser` is the browser-API module (no decorator shadows it)."""
    import pyweb
    from pyweb.browser import storage
    assert storage is _b.storage
    assert pyweb.browser is _b and pyweb.browser_api is _b


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


COUNTER = ("from pyweb import App\napp = App()\n@app.page('/')\ndef Home():\n    count = 0\n"
           "    def inc():\n        count += 1\n    <button onclick={inc}>Count: {count}</button>\n"
           "@app.page('/about')\ndef About():\n    <p>static</p>\n")


def test_build_writes_hashed_split_artifacts(tmp_path):
    from pyweb.compiler import compile_source
    compiled = compile_source(COUNTER)
    manifest = _build.build(compiled, str(tmp_path), source=COUNTER)
    page = manifest["pages"]["Home"]
    assert page["js"].startswith("Home.") and page["js"].endswith(".js")
    assert page["runtime"].startswith("runtime.") and page["runtime"] != "runtime.js"
    js = open(str(tmp_path / "static" / page["js"])).read()
    assert f'from"./{page["runtime"]}"' in js.replace(" ", "")   # page imports the hashed runtime
    assert os.path.exists(str(tmp_path / "static" / page["runtime"]))
    html = open(str(tmp_path / "server" / "Home.html")).read()
    assert f'/static/{page["js"]}' in html
    assert manifest["pages"]["About"]["js"] == ""                  # static page ships no JS
    assert "<script" not in open(str(tmp_path / "server" / "About.html")).read()
    assert os.path.exists(str(tmp_path / "Dockerfile"))
    assert open(str(tmp_path / "app.pyweb")).read() == COUNTER
    assert manifest["app"] == "app.pyweb" and manifest["runtime_gzip_bytes"] > 0


def test_minifier_never_touches_literals():
    js = ('const a = "Count: " + b; // c\nconst u = "https://x.y/z";\n'
          "const r = /a\\/b[/]c/g; const t = `x ${a + \"}\"} y`;\nreturn a\n+b;")
    out = _build.minify_js(js)
    for lit in ['"Count: "', '"https://x.y/z"', "/a\\/b[/]c/g", '`x ${a + "}"} y`']:
        assert lit in out, lit
    assert "// c" not in out


def test_content_hash_stable():
    assert _build.content_hash("x") == _build.content_hash("x")
    assert _build.content_hash("x") != _build.content_hash("y")
