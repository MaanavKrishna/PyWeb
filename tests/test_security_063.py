"""0.6.3 security fixes: each test reproduces the problem the audit found."""


import pytest

from pyweb.testing import TestClient

GUARDED = '''from pyweb import App, auth, server

app = App()


@server
@auth.required
def secret_thing() -> str:
    return "secret"


@server(rate="2/minute")
def limited() -> int:
    return 1


@app.page("/")
def Home():
    <p>hi</p>
'''


def test_guarded_functions_need_a_session_even_without_a_configured_secret(monkeypatch):
    monkeypatch.delenv("PYWEB_AUTH_SECRET", raising=False)
    c = TestClient(source=GUARDED)
    from pyweb.testing import RPCError
    with pytest.raises(RPCError) as err:
        c.rpc("secret_thing")
    assert err.value.code == "unauthenticated"


def test_guarded_functions_fail_closed_in_production_without_a_secret(monkeypatch):
    monkeypatch.delenv("PYWEB_AUTH_SECRET", raising=False)
    monkeypatch.setenv("PYWEB_ENV", "production")
    c = TestClient(source=GUARDED)
    from pyweb.testing import RPCError
    with pytest.raises(RPCError) as err:
        c.rpc("secret_thing")
    assert err.value.code == "unauthenticated" and "PYWEB_AUTH_SECRET" in str(err.value)


def test_serve_refuses_to_start_without_a_secret(monkeypatch):
    from pyweb import config
    monkeypatch.delenv("PYWEB_AUTH_SECRET", raising=False)
    monkeypatch.delenv("PYWEB_ENV", raising=False)
    with pytest.raises(SystemExit, match="PYWEB_AUTH_SECRET"):
        config.require_secret()
    monkeypatch.setenv("PYWEB_ENV", "development")
    config.require_secret()
    monkeypatch.delenv("PYWEB_ENV")
    monkeypatch.setenv("PYWEB_AUTH_SECRET", "x" * 64)
    config.require_secret()


def test_the_dev_key_is_random_and_private(tmp_path, monkeypatch):
    from pyweb import context
    monkeypatch.setenv("PYWEB_CACHE_DIR", str(tmp_path))
    a = context._derive_dev_secret()
    assert a == context._derive_dev_secret() and len(a) == 64
    assert (tmp_path / "dev-secret").stat().st_mode & 0o077 == 0
    monkeypatch.setenv("PYWEB_CACHE_DIR", str(tmp_path / "other"))
    assert context._derive_dev_secret() != a          # nothing about the machine decides it


SPREAD = '''from pyweb import App, server

app = App()

@server
def evil() -> dict:
    return {"ONCLICK": "alert(1)", "x onmouseover=alert(2) y": "1", "HREF": "javascript:alert(3)",
            "srcdoc": "<script>alert(4)</script>", "data": "javascript:alert(5)", "title": "fine"}

@app.page("/")
def Home():
    attrs = evil()
    <a id="link" {**attrs}>x</a>
'''


@pytest.mark.parametrize("verify", ["0", "1"])
def test_spread_attributes_cannot_inject_handlers_or_urls(monkeypatch, verify):
    monkeypatch.setenv("PYWEB_SSR_VERIFY", verify)
    html = TestClient(source=SPREAD).get("/").text
    tag = html[html.index('<a id="link"'):html.index(">x</a>")]
    low = tag.lower()
    assert "onclick" not in low and "onmouseover" not in low and "javascript" not in low and "srcdoc" not in low
    assert 'title="fine"' in tag
