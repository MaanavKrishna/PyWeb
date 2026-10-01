"""Website chrome contract for the Tailwind-based docs builder (v4).

Asserts on pure helpers in ``website/build.py`` without writing to
``website/dist/``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location(
    "website_build_v4", str(ROOT / "website" / "build.py")
)
wb = importlib.util.module_from_spec(spec)
sys.modules["website_build_v4"] = wb
spec.loader.exec_module(wb)


def test_nav_groups_cover_every_page_once():
    flat = [href for _, items in wb.NAV_GROUPS for _, href in items]
    assert sorted(flat) == sorted(wb.NAV_INDEX)
    assert len(set(flat)) == len(flat) == len(wb.NAV)


def test_header_is_compact_and_tailwind():
    html = wb.nav_html("guide.html")
    assert "cdn.tailwindcss.com" not in html  # config lives in page head
    assert html.count("<a href=") <= 7  # logo + 5 links + github
    for label in ("Guide", "Playground", "Examples", "Benchmarks", "API"):
        assert label in html
    assert "hideable" not in html
    assert "nav class=\"ml-auto" in html or 'ml-auto hidden' in html


def test_sidebar_marks_active_page_once():
    html = wb.side_html("rpc.html")
    assert "rpc.html" in html
    assert html.count("lg:font-bold") == 1
    for group in ("Start", "Core", "Runtime", "Ship"):
        assert group in html


def test_pager_walks_nav_order():
    first = wb.NAV[0][1]
    assert "Previous" not in wb.pager_html(first)
    assert "Next" in wb.pager_html(first)
    last = wb.NAV[-1][1]
    assert "Previous" in wb.pager_html(last)
    assert "Next" not in wb.pager_html(last)
    mid = wb.NAV[len(wb.NAV) // 2][1]
    both = wb.pager_html(mid)
    assert "Previous" in both and "Next" in both
    assert wb.pager_html("example-counter.html") == ""


def test_page_shell_uses_tailwind_and_theme_vars():
    html = wb.page("guide.html", "T", "D", "<section>x</section>",
                   active="guide.html")
    for needle in (
        "cdn.tailwindcss.com", "tailwind.config", "bg-base", "text-ink",
        "border-line", "bg-panel", 'id="site-search"', "assets/style.css",
        "assets/site.js", "Skip to content", "<aside", "<footer",
    ):
        assert needle in html, needle


def test_index_renders_wide_without_sidebar():
    _title, _desc, body, _active, wide = wb.PAGES["index.html"]
    assert wide is True
    html = wb.page("index.html", "T", "D", body, active="index.html", wide=wide)
    assert "<aside" not in html
    assert "Every layer" in html


def test_stylesheet_keeps_components_drops_chrome():
    for token in (".pager", ".tabset", ".codehead", ".copybtn", ".spec",
                  ".glow", "[data-theme=light]", "--grad"):
        assert token in wb.CSS, token
    for dead in (".side{", "header.top", ".footgrid", ".searchbox", ".docgrid",
                 ".hero-stats", "nav.main", ".hideable"):
        assert dead not in wb.CSS, dead
