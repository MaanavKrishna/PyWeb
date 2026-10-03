"""The dashboard example: live queries feeding a Chart.js chart, in two windows."""

import json
import shutil
from pathlib import Path

from playwright.sync_api import expect

from pyweb.testing import serve
from tests.e2e.conftest import ready

EXAMPLE = Path(__file__).parent.parent.parent / "examples" / "dashboard"


def chart_data(page):
    version = json.loads((EXAMPLE / "pyweb.lock").read_text())["packages"]["chart.js"]["version"]
    return page.evaluate(f"""async () => {{
        const {{ Chart }} = await import("/static/vendor/chart.js@{version}/dist/chart.js");
        return Chart.getChart(document.getElementById("chart")).data.datasets[0].data;
    }}""")


def test_dashboard_updates_every_window(browser, tmp_path, monkeypatch):
    app = tmp_path / "dashboard"
    shutil.copytree(EXAMPLE, app)
    monkeypatch.chdir(app)
    errors = []
    with serve(str(app / "app.pyweb")) as url:
        a, b = browser.new_page(), browser.new_page()
        for page in (a, b):
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(url)
            ready(page)
        expect(b.locator("#count")).to_have_text("12")
        before = chart_data(b)
        assert len(before) == 4
        a.select_option("select", "Chair")
        a.fill("input[type=number]", "100")
        a.click("text=Record sale")
        expect(b.locator("#count")).to_have_text("13")
        expect(b.locator("#recent li").first).to_contain_text("Chair")
        expect(b.locator("#revenue")).to_contain_text("$")
        after = chart_data(b)
        assert after[0] == before[0] + 100          # Chair is first alphabetically
        b.click("#simulate")
        expect(a.locator("#count")).to_have_text("23")
    assert errors == []
