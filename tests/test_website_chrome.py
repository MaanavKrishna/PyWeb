"""Website chrome: sidebar, pager, and theme contract for the docs builder.

These tests import pure helpers from ``website/build.py`` without writing
to ``website/dist/`` — the builder already emits real compiler artifacts
at import time, so this file only asserts on the chrome contract.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location(
    "website_build", str(ROOT / "website" / "build.py")
)
wb = importlib.util.module_from_spec(spec)
sys.modules["website_build"] = wb
spec.loader.exec_module(wb)


def test_nav_groups_cover_every_page_once():
    flat = [href for _, items in wb.NAV_GROUPS for _, href in items]
    assert sorted(flat) == sorted(wb.NAV_INDEX)
    assert len(set(flat)) == len(flat) == len(wb.NAV)


def test_sidebar_marks_active_page():
    html = wb.side_html("rpc.html")
    assert 'href="rpc.html" class="on"' in html
    assert html.count('class="on"') == 1
    for group in ("Start", "Core", "Runtime", "Ship"):
        assert group in html


def test_pager_walks_nav_order():
    first = wb.NAV[0][1]
    assert 'Previous' not in wb.pager_html(first)
    assert 'Next' in wb.pager_html(first)
    last = wb.NAV[-1][1]
    assert 'Previous' in wb.pager_html(last)
    assert 'Next' not in wb.pager_html(last)
    mid = wb.NAV[len(wb.NAV) // 2][1]
    both = wb.pager_html(mid)
    assert 'Previous' in both and 'Next' in both
    assert wb.pager_html("example-counter.html") == ""


def test_page_shell_has_chrome_and_search_wiring():
    html = wb.page("guide.html", "T", "D", "<section class=block>x</section>",
                   active="guide.html")
    for needle in (
        'class="side"', 'class="pager"', 'id="site-search"',
        'assets/style.css', 'assets/site.js', 'Skip to content',
        'aria-label="Docs"', "<footer>",
    ):
        assert needle in html, needle


def test_stylesheet_has_new_design_tokens():
    for token in ("--grad", "--side", ".footgrid", ".pager", ".eyebrow",
                  ".statband", ".step-n", "[data-theme=light]"):
        assert token in wb.CSS, token
    assert ".docgrid" not in wb.CSS  # dead layout class removed
