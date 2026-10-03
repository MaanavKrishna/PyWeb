# Testing

Apps created with `pyweb new` include a `test_app.py` that checks the
home page renders; run `pytest` in the app folder and add tests next to it.

## `TestClient`: fast, in-process, no browser

`pyweb.testing.TestClient` loads your app exactly like the production
server and lets you request pages and call server functions. Cookies
persist between calls, so sessions work.

```python
from pyweb.testing import TestClient
from pyweb import RPCError
import pytest


def test_notes():
    client = TestClient("app.pyweb")

    assert client.get("/").status == 303            # redirects to /login

    assert client.rpc("sign_in", name="ada") is True
    page = client.get("/")
    assert page.status == 200
    assert "Notes for ada" in page.text

    with pytest.raises(RPCError) as e:
        client.rpc("add_note", body="")
    assert e.value.code == "validation_error"
```

| API | |
|---|---|
| `TestClient(path)` / `TestClient(source="...")` | load an app file or source text |
| `.get(path)`, `.post(path, data)`, `.request(method, path, body, headers)` | return a response with `.status`, `.text`, `.json()`, `.header(name)` |
| `.rpc(name, **args)` | call a server function; returns the result or raises `RPCError` |
| `.cookies` | the cookie jar |
| `.app.module` | the executed app module, for direct access to its objects |

## Real browser tests

`pyweb.testing.serve(path)` runs the app on a free port. Combine it with
[Playwright](https://playwright.dev/python/) to test interactivity:

```python
from playwright.sync_api import expect, sync_playwright
from pyweb.testing import serve


def test_counter_in_a_browser():
    with serve("app.pyweb") as url, sync_playwright() as p:
        page = p.chromium.launch().new_page()
        page.goto(url)
        page.wait_for_selector("[data-pw-ready]")   # set once the page is interactive
        page.click("text=Count: 0")
        expect(page.locator("button")).to_have_text("Count: 1")
```

`serve()` applies the production Content-Security-Policy, so your
tests also prove the app works under it.

## Compile-time checks

`pyweb check app.pyweb` compiles the app and runs the security checks;
it exits non-zero on errors, which makes it a useful first CI step.
`compile_source(text)` from `pyweb.compiler` gives you the compiled
pages (HTML, JS, signals, placement) if you want to assert on them.
