"""The website playground, running PyWeb on Pyodide in a real browser.

Needs an unpacked ``pyodide`` npm package (``npm pack pyodide@<version>``)
in PYWEB_PYODIDE_DIR; CI sets it up. Skipped otherwise.
"""

import functools
import http.server
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from playwright.sync_api import expect

ROOT = Path(__file__).parent.parent.parent
PYODIDE = os.environ.get("PYWEB_PYODIDE_DIR")
pytestmark = pytest.mark.skipif(not PYODIDE or not os.path.isdir(PYODIDE),
                                reason="set PYWEB_PYODIDE_DIR to an unpacked pyodide npm package")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    subprocess.run([sys.executable, str(ROOT / "website" / "build.py"), str(out)], check=True,
                   env={**os.environ, "PYWEB_PYODIDE_DIR": PYODIDE}, capture_output=True)

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(out)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def open_playground(page, url, fragment=""):
    page.goto(f"{url}/playground.html{fragment}")
    expect(page.locator("#pg-status")).to_contain_text("Compiled and rendered", timeout=90000)
    return page.frame_locator("#pg-frame")


def test_counter_runs_hydrated_and_shows_compiler_output(page, site):
    app = open_playground(page, site)
    expect(app.locator("[data-pw-root]")).to_have_attribute("data-pw-mode", "hydrated")
    app.locator("#inc").click()
    app.locator("#inc").click()
    expect(app.locator("#inc")).to_have_text("Count: 2")
    page.click("text=JavaScript")
    expect(page.locator("#pg-js")).to_contain_text('$mount("Home", Home)')
    page.click("text=What runs where")
    expect(page.locator("#pg-place td.name", has_text="increment")).to_be_visible()


def test_edits_recompile_and_errors_show_line_and_fix(page, site):
    app = open_playground(page, site)
    editor = page.locator("#pg-source")
    editor.fill(editor.input_value().replace("<h1>Counter</h1>", "<h1>Edited</h1>"))
    expect(app.locator("h1")).to_have_text("Edited")
    editor.fill(editor.input_value().replace("<h1>Edited</h1>", "<h1>Edited</div>"))
    expect(page.locator("#pg-error")).to_contain_text("mismatched </div>")
    expect(page.locator("#pg-error em")).to_contain_text("closing tags")
    expect(page.locator("#pg-gutter b")).to_have_text("19")


def test_server_functions_sessions_and_navigation(page, site):
    app = open_playground(page, site, "#auth")
    app.locator("a", has_text="Create one").click()
    expect(page.locator("#pg-url")).to_have_value("/register")
    app.locator("#name").fill("Ada")
    app.locator("#email").fill("ada@example.com")
    app.locator("#password").fill("correct horse")
    app.locator("#register").click()  # @server register(), then window.location.href = "/account"
    expect(page.locator("#pg-url")).to_have_value("/account")
    expect(app.locator("h1")).to_contain_text("Ada")


def test_live_updates_and_server_search(page, site):
    app = open_playground(page, site, "#chat")
    app.locator("a", has_text="#python").click()
    app.locator("#draft").fill("hello playground")
    app.locator("#draft").press("Enter")
    expect(app.locator(".msg")).to_have_text(["guesthello playground"], timeout=8000)
    page.select_option("#pg-example", "showcase")
    expect(app.locator("#results li")).to_have_count(5)
    app.locator("#q").fill("mo")
    expect(app.locator("#results li")).to_have_count(2)


def test_share_link_round_trip(page, site):
    open_playground(page, site)
    editor = page.locator("#pg-source")
    editor.fill(editor.input_value().replace("Counter", "Shared counter"))
    page.click("#pg-share")
    expect(page.locator("#pg-status")).to_contain_text("Link")
    link = page.url
    assert "#code=" in link
    page.goto("about:blank")
    app = open_playground(page, site, "#" + link.split("#", 1)[1])
    expect(app.locator("h1")).to_have_text("Shared counter")


def test_multi_page_site_with_a_layout(page, site):
    app = open_playground(page, site, "#site")
    expect(app.locator(".site-nav a[aria-current=page]")).to_have_text("Home")
    app.locator("a", has_text="low light").click()
    expect(page.locator("#pg-url")).to_have_value("/plants?light=low")
    expect(app.locator("#plants li")).to_have_count(3)


def test_ai_chat_with_the_demo_model(page, site):
    app = open_playground(page, site, "#ai-chat")
    app.locator("#prompt").fill("Hi there")
    app.locator("#prompt").press("Enter")
    expect(app.locator(".bubble.assistant strong").first).to_have_text("Hi there", timeout=20000)
    expect(app.locator("#send")).to_be_visible()


def test_editor_highlighting_console_and_tools(page, site):
    app = open_playground(page, site)
    expect(page.locator("#pg-hl .k", has_text="def").first).to_be_visible()      # syntax highlighting
    editor = page.locator("#pg-source")
    editor.fill(editor.input_value().replace("        count += step", "        print('clicked', count)\n        count += step"))
    expect(page.locator("#pg-draft")).to_be_visible()                           # marked as edited
    page.click("#pg-run")
    expect(app.locator("[data-pw-root]")).to_have_attribute("data-pw-mode", "hydrated")
    app.locator("#inc").click()
    expect(page.locator("#pg-count")).to_be_visible()                           # unseen console output
    page.click(".pg-tabs >> text=Console")
    expect(page.locator(".pg-row.browser .pg-text")).to_have_text("clicked 0")  # print() in a handler runs in the browser
    page.reload()                                                               # edits survive a reload
    expect(page.locator("#pg-status")).to_contain_text("Restored", timeout=90000)
    assert "print('clicked', count)" in page.locator("#pg-source").input_value()
    with page.expect_download() as dl:
        page.click("#pg-download")
    assert dl.value.suggested_filename == "app.pyweb"
    page.click("#pg-reset")
    expect(page.locator("#pg-draft")).to_be_hidden()
    assert "print(" not in page.locator("#pg-source").input_value()


def test_server_calls_and_errors_are_logged(page, site):
    app = open_playground(page, site, "#chat")
    app.locator("a", has_text="#python").click()
    app.locator("#draft").fill("hello console")
    app.locator("#draft").press("Enter")
    expect(app.locator(".msg")).to_have_count(1, timeout=8000)
    page.click(".pg-tabs >> text=Console")
    expect(page.locator(".pg-row.call .pg-text", has_text="send(")).to_be_visible()
    expect(page.locator(".pg-row.call .pg-detail").first).to_contain_text("200")
    page.click(".pg-tabs >> text=JavaScript")
    expect(page.locator("#pg-jsinfo")).to_contain_text("gzipped")
    page.click(".pg-tabs >> text=Preview")
    page.click(".pg-sizes button[data-width='390']")
    assert page.locator("#pg-frame").evaluate("f => f.getBoundingClientRect().width") <= 392
