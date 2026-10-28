"""@app.route / @app.middleware / robots.txt / sitemap.xml."""


import pytest

from pyweb.testing import TestClient

APP = '''
import time
from pyweb import App, NotFound, session, server
from pyweb.routes import csv, json, redirect, text

app = App(title="Routes", base_url="https://example.com")
ITEMS = {1: {"id": 1, "name": "pen"}}
LOG = []


@app.middleware
def timing(request, call_next):
    LOG.append(("before", request.path.split("?")[0]))
    response = call_next(request)
    if response is not None:
        response.headers["X-Mw"] = "1"
    return response


@app.middleware
def block(request, call_next):
    if request.path.startswith("/blocked"):
        return json({"error": "no"}, status=451)
    return call_next(request)


@app.route("/api/items/{id:int}")
def get_item(id: int):
    if id not in ITEMS:
        raise NotFound()
    return ITEMS[id]


@app.route("/api/items", methods=["GET", "POST"])
def items(body: dict = None, q: str = ""):
    if body is not None:
        new = {"id": max(ITEMS) + 1, "name": body["name"]}
        ITEMS[new["id"]] = new
        return json(new, status=201)
    return [i for i in ITEMS.values() if q in i["name"]]


@app.route("/api/items/{id:int}", methods=["DELETE"], login=True)
def remove(id: int):
    ITEMS.pop(id, None)


@app.route("/admin/export.csv", roles=["admin"])
def export():
    return csv(ITEMS.values(), filename="items.csv")


@app.route("/hello/{name}")
def hello(name):
    return f"hi {name}"


@app.route("/files/{rest:path}")
def files(rest):
    return text(rest)


@app.route("/go")
def go():
    return redirect("/hello/there")


@app.route("/public-api", methods=["POST"], cors=["https://partner.example"])
def public(body: dict):
    return {"got": body}


@app.route("/limited", rate="2/minute")
def limited():
    return "ok"


@app.route("/boom")
def boom():
    raise RuntimeError("kaboom")


@server
def sign_in(roles: list) -> bool:
    session.login("ada", roles=roles)
    return True


@app.sitemap
def more():
    return ["/hello/world"]


@app.page("/")
def Home():
    <p>home</p>


@app.page("/secret", login=True)
def Secret():
    <p>s</p>


@app.page("/thing/{x}")
def Thing(x):
    <p>{x}</p>
'''


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PYWEB_ENV", "development")
    c = TestClient(source=APP)
    yield c


def test_routes_answer_json_text_and_typed_params(client):
    r = client.get("/api/items/1")
    assert r.status == 200 and r.json() == {"id": 1, "name": "pen"} and r.header("X-Mw") == "1"
    assert client.get("/api/items/x").status == 404                       # not an int: no match
    assert client.get("/api/items/9").status == 404
    assert client.get("/hello/bob").text == "hi bob"
    assert client.get("/files/a/b/c.txt").text == "a/b/c.txt"
    assert client.get("/api/items?q=pe").json() == [{"id": 1, "name": "pen"}]
    r = client.post("/api/items", {"name": "cup"})
    assert r.status == 201 and r.json()["name"] == "cup"
    r = client.get("/go")
    assert r.status == 303 and r.header("Location") == "/hello/there"


def test_methods_guards_and_csrf(client):
    r = client.request("PUT", "/api/items/1")
    assert r.status == 405 and "DELETE" in r.header("Allow") and "GET" in r.header("Allow")
    assert client.delete("/api/items/1").status == 401                     # login=True
    assert client.rpc("sign_in", roles=[]) is True
    assert client.get("/admin/export.csv").status == 403                   # roles=["admin"]
    other = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    assert client.delete("/api/items/1", headers=other).status == 403      # another site, the visitor's cookies
    assert client.rpc("sign_in", roles=["admin"]) is True
    r = client.get("/admin/export.csv")
    assert r.status == 200 and r.header("Content-Disposition") == 'attachment; filename="items.csv"'
    assert r.text.splitlines() == ["id,name", "1,pen"]
    assert client.delete("/api/items/1").status == 204
    assert client.get("/api/items/1").status == 404


def test_cors_and_preflight(client):
    pre = client.request("OPTIONS", "/public-api", headers={"Origin": "https://partner.example",
                                                            "Access-Control-Request-Method": "POST"})
    assert pre.status == 204 and pre.header("Access-Control-Allow-Origin") == "https://partner.example"
    r = client.post("/public-api", {"a": 1}, headers={"Origin": "https://partner.example",
                                                      "Sec-Fetch-Site": "cross-site"})
    assert r.status == 200 and r.json() == {"got": {"a": 1}}
    assert r.header("Access-Control-Allow-Origin") == "https://partner.example"
    r = client.post("/public-api", {"a": 1}, headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert r.status == 403


def test_rate_limits_errors_and_middleware(client):
    assert [client.get("/limited").status for _ in range(3)] == [200, 200, 429]
    assert client.get("/blocked/x").status == 451                          # a middleware answered
    with pytest.raises(RuntimeError, match="kaboom"):                      # debug: the error surfaces
        client.get("/boom")
    log = client.site.app.module.LOG
    assert ("before", "/api/items/1") in log or ("before", "/limited") in log


def test_robots_and_sitemap(client):
    robots = client.get("/robots.txt").text
    assert "Disallow: /__pyweb/" in robots and "Sitemap: https://example.com/sitemap.xml" in robots
    xml = client.get("/sitemap.xml").text
    assert "<loc>https://example.com/</loc>" in xml and "<loc>https://example.com/hello/world</loc>" in xml
    assert "/secret" not in xml and "/thing/" not in xml                   # guarded or with parameters


def test_route_definitions_are_checked():
    from pyweb import App
    app = App()
    with pytest.raises(ValueError, match="use one of"):
        app.route("/x/{id:number}")(lambda id: id)
    app.route("/y")(lambda: 1)
    with pytest.raises(ValueError, match="already answered"):
        app.route("/y")(lambda: 2)
    from pyweb.routes import csv
    body = csv([{"a": "=1+1", "b": None}]).body
    assert body.splitlines()[1] == "'=1+1,"                                # no formulas in spreadsheets


def test_api_tokens_on_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("PYWEB_ENV", "development")
    src = f'''
from pyweb import App
app = App(database="sqlite:///{tmp_path / "t.db"}")
auth = app.use_auth()

@app.route("/api/me", tokens=True, login=True)
def me():
    return {{"user": auth.user().email}}

@app.route("/api/other", login=True)
def other():
    return "x"

@app.page("/")
def Home():
    <p>x</p>
'''
    c = TestClient(source=src)
    user = c.login("ada@example.com")
    kit = c.site.server._auth_kit()
    raw = kit.create_token(user, "script", scopes=["me"])[1]
    if raw is None:
        pytest.skip("no token API")
    c.cookies.clear()
    bearer = {"Authorization": f"Bearer {raw}"}
    assert c.request("GET", "/api/me", headers=bearer).json() == {"user": "ada@example.com"}
    assert c.request("GET", "/api/other", headers=bearer).status == 401    # tokens only where allowed


def test_a_plugin_brings_its_routes_and_server_functions(tmp_path, monkeypatch):
    monkeypatch.setenv("PYWEB_ENV", "development")
    (tmp_path / "shop_plugin.py").write_text('''
from pyweb.plugins import Plugin
shop = Plugin("shop")

@shop.server
def price(sku: str) -> int:
    return 42

@shop.route("/shop/health")
def health():
    return {"ok": True}

@shop.page("/shop")
def shop_page():
    return "<h1>Shop</h1>"
''')
    monkeypatch.syspath_prepend(str(tmp_path))
    src = '''from pyweb import App
from shop_plugin import shop
app = App(plugins=[shop])

@app.page("/")
def Home():
    <p>x</p>
'''
    c = TestClient(source=src)
    assert c.get("/shop/health").json() == {"ok": True}
    r = c.get("/shop")
    assert r.text == "<h1>Shop</h1>" and r.header("Content-Type").startswith("text/html")
    assert c.rpc("price", sku="a") == 42
