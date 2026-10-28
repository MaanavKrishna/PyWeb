"""<ErrorBoundary>: a broken part shows its fallback; the rest of the page keeps working."""

from pyweb.testing import serve

from .conftest import until

APP = '''
from pyweb import App, ErrorBoundary

app = App()


def broken():
    raise RuntimeError("chart data missing")          # on the server, and again in the browser


@app.page("/")
def Home():
    count = 0

    def boom():
        raise RuntimeError("click failed")

    def add():
        count += 1

    <h1>Dashboard</h1>
    <ErrorBoundary fallback="The chart couldn't load.">
        <p>{broken()}</p>
    </ErrorBoundary>
    <ErrorBoundary fallback="This widget stopped working.">
        <button id="boom" onclick={boom}>Break</button>
    </ErrorBoundary>
    <button id="add" onclick={add}>Clicked {count}</button>
'''


def test_server_and_browser_errors_stay_inside_the_boundary(page, tmp_path, monkeypatch):
    monkeypatch.setenv("PYWEB_ENV", "development")
    (tmp_path / "app.pyweb").write_text(APP)
    with serve(str(tmp_path / "app.pyweb"), rate_limit=False) as url:
        page.goto(url + "/")
        page.wait_for_selector("[data-pw-ready]", state="attached")
        assert "The chart couldn't load." in page.text_content("body")        # rendered on the server
        page.click("#boom")
        until(page, "document.body.textContent.includes('This widget stopped working.')")
        assert page.locator("#boom").is_hidden()
        page.click("#add")                                                     # the rest still works
        until(page, "document.querySelector('#add').textContent.includes('Clicked 1')")
    caught = [e for e in page.errors if "ErrorBoundary" in e or "click failed" in e or "chart data" in e]
    assert caught                                                              # logged, on purpose
    page.errors[:] = [e for e in page.errors if e not in caught]
