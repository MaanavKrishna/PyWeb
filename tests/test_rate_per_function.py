"""@server(rate=..., per=...): limits for one server function."""

import pytest

from pyweb.decorators import parse_rate
from pyweb.testing import TestClient

APP = '''
from pyweb import App, server

app = App()


@server(rate="2/minute")
def per_user():
    return 1


@server(rate="3/hour", per="all")
def everyone():
    return 1


@server(rate="1/10s", per="ip")
def per_ip():
    return 1


@server
def unlimited():
    return 1


@app.page("/")
def Home():
    <p>hi</p>
'''


@pytest.fixture
def app_file(tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(APP)
    return str(path)


def call(client, name, ip="1.1.1.1"):
    return client.request("POST", f"/__pyweb/rpc/{name}", b"{}", {"Content-Type": "application/json",
                                                                 "X-Test-Client": ip}).status


def test_parse_rate():
    assert parse_rate("10/minute") == (10, 60, "user")
    assert parse_rate("5 / seconds", "ip") == (5, 1, "ip")
    assert parse_rate("30/5m", "all") == (30, 300, "all")
    assert parse_rate("1/day") == (1, 86400, "user")
    for bad in ("10", "0/minute", "ten/minute", "5/fortnight"):
        with pytest.raises(ValueError, match="rate="):
            parse_rate(bad)
    with pytest.raises(ValueError, match="per="):
        parse_rate("1/minute", "org")


def test_limits_apply_per_signed_in_user(app_file):
    ann, bo = TestClient(app_file), TestClient(app_file)
    ann.login("ann@example.com")
    bo.login("bo@example.com")
    assert [call(ann, "per_user") for _ in range(3)] == [200, 200, 429]
    assert [call(bo, "per_user") for _ in range(2)] == [200, 200]           # Bo has his own budget
    assert call(ann, "unlimited") == 200


def test_visitors_who_arent_signed_in_are_counted_by_address(app_file, monkeypatch):
    from pyweb.runtime import server as S
    monkeypatch.setattr(S, "client_ip", lambda req: req.headers.get("X-Test-Client", "?"))
    c = TestClient(app_file)
    assert [call(c, "per_user", "1.1.1.1") for _ in range(3)] == [200, 200, 429]
    assert call(c, "per_user", "2.2.2.2") == 200
    assert [call(c, "per_ip", "3.3.3.3"), call(c, "per_ip", "3.3.3.3"), call(c, "per_ip", "4.4.4.4")] == [200, 429, 200]
    assert [call(c, "everyone", ip) for ip in ("5.5.5.5", "6.6.6.6", "7.7.7.7", "8.8.8.8")] == [200, 200, 200, 429]
