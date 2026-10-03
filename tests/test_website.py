"""The website builds from docs/ and examples/, and has no broken internal links."""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("pyweb_website_build", ROOT / "website" / "build.py")
    wb = importlib.util.module_from_spec(spec)
    sys.modules["pyweb_website_build"] = wb
    spec.loader.exec_module(wb)
    out = tmp_path_factory.mktemp("site")
    pages = wb.build(str(out))
    return wb, out, pages


def test_every_doc_is_published(site):
    wb, out, _ = site
    docs = sorted(p.name for p in (ROOT / "docs").glob("[0-9]*.md"))
    assert sorted(f for f, *_ in wb.DOCS) == docs
    for _f, slug, _t, _g in wb.DOCS:
        assert (out / f"{slug}.html").exists()


def test_internal_links_and_anchors_resolve(site):
    _wb, out, _ = site
    ids = {}
    for page in out.glob("*.html"):
        ids[page.name] = set(re.findall(r'id="([^"]+)"', page.read_text()))
    broken = []
    for page in out.glob("*.html"):
        for href in re.findall(r'href="([^"]+)"', page.read_text()):
            if href.startswith(("http", "mailto:", "assets/", "demos/")) or href == "#":
                continue
            target, _, frag = href.partition("#")
            target = target or page.name
            if target not in ids:
                broken.append((page.name, href))
            elif frag and frag not in ids[target]:
                broken.append((page.name, href))
    assert not broken, broken


def test_pyweb_blocks_show_compiler_output(site):
    _wb, out, _ = site
    import html as _html
    raw = (out / "tutorial.html").read_text()
    assert raw.count('class="compiled"') >= 4
    text = _html.unescape(re.sub(r"<[^>]+>", "", raw))
    assert "async function save()" in text and '$rpc("add_note"' in text


def test_live_demos_are_real_compiled_apps(site):
    _wb, out, _ = site
    for name in ("counter", "todo"):
        d = out / "demos" / name
        assert 'data-pw-root="Home"' in (d / "index.html").read_text()
        assert (d / "static" / "runtime.js").exists() and (d / "static" / "Home.js").exists()


def test_search_index_and_sitemap(site):
    _wb, out, pages = site
    import json
    index = json.loads((out / "search.json").read_text())
    assert any(e["url"] == "server-functions.html" for e in index)
    sitemap = (out / "sitemap.xml").read_text()
    assert sitemap.count("<url>") == len(pages)


def test_playground_page_and_bundle(site):
    import json
    import zipfile
    wb, out, _ = site
    html = (out / "playground.html").read_text()
    config = json.loads(re.search(r'<script id="pg-config" type="application/json">(.*?)</script>', html, re.S).group(1))
    import os
    expected = "pyodide/" if os.environ.get("PYWEB_PYODIDE_DIR") else wb.PYODIDE_CDN
    assert config["pyodide"] == expected and set(config["examples"]) == {n for n, _ in wb.PLAYGROUND_EXAMPLES}
    assert config["examples"]["counter"]["source"] == (ROOT / "examples" / "counter" / "app.pyweb").read_text()
    names = zipfile.ZipFile(out / "playground" / "pyweb.zip").namelist()
    assert "pyweb/__init__.py" in names and "pyweb/runtime/browser/runtime.js" in names
    assert not any("__pycache__" in n for n in names)
    for name in config["static"]:
        assert (out / "playground" / "static" / name).exists()
    compile((out / "playground" / "host.py").read_text(), "host.py", "exec")
    assert 'href="playground.html"' in (out / "index.html").read_text()
