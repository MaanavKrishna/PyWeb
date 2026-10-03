"""Multi-page apps: layouts, error pages, head tags, typed query parameters, current links."""

import json
import re
import textwrap

import pytest

from pyweb.compiler import compile_source
from pyweb.compiler.errors import CompileError
from pyweb.testing import TestClient

APP = textwrap.dedent('''
    from pyweb import App, head

    app = App(title="Shop", base_url="https://shop.test", description="A small shop")
    PRODUCTS = {1: "Lamp", 2: "Desk"}


    @app.layout
    def Shell(children):
        menu = False

        def toggle():
            menu = not menu

        <div class="shell">
            <nav>
                <a href="/">Home</a>
                <a href="/products">Products</a>
                <a href="/products/1">Lamp</a>
                <a href="/admin/users">Users</a>
                <a href="/bare">Bare</a>
                <a href="/nope">Broken</a>
                <a href="/products" aria-current="step">Step</a>
                <button id="menu" onclick={toggle}>{"close" if menu else "menu"}</button>
            </nav>
            <main>{children}</main>
        </div>


    @app.layout("/admin")
    def Admin(children):
        <section class="admin">{children}</section>


    @app.page("/", title="Home")
    def Home():
        count = 0

        def inc():
            count += 1

        <button id="inc" onclick={inc}>{count}</button>


    @app.page("/products", description="All products")
    def Products(q: str = "", page: int = 1, tags: list[str] = [], sale: bool = False):
        items = [p for p in PRODUCTS.values() if q.lower() in p.lower()]

        <p id="info">{q}|{page}|{tags}|{sale}</p>
        <ul>
            for name in items:
                <li>{name}</li>
        </ul>


    @app.page("/products/{pid}")
    def Product(pid: int):
        name = PRODUCTS[pid]
        head(title=name + " - Shop", description="Buy a " + name, image="/static/" + name + ".png")

        <h1 id="name">{name}</h1>


    @app.page("/search")
    def Search(q: str):
        <p id="q">{q}</p>


    @app.page("/admin/users")
    def Users():
        <p id="users">users</p>


    @app.page("/bare", layout=None, noindex=True)
    def Bare():
        <p id="bare">bare</p>


    @app.page("/boom")
    def Boom():
        value = 1 / 0
        <p>{value}</p>


    @app.error(404)
    def Missing(path, status):
        <h1 id="missing">{status}: nothing at {path}</h1>


    @app.error(500)
    def Broken(request_id):
        <h1 id="broken">Sorry ({request_id})</h1>
''').lstrip()


@pytest.fixture
def client(tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(APP)
    return TestClient(str(path))


def root_names(html):
    return re.findall(r'data-pw-root="(\w+)"', html)


# ------------------------------------------------------------- layouts

def test_layouts_wrap_pages_by_prefix_outermost_first(client):
    html = client.get("/admin/users").text
    assert root_names(html) == ["Shell", "Admin", "Users"]
    assert '<main><div data-pw-slot="Shell" style="display:contents"><div data-pw-root="Admin"' in html
    assert root_names(client.get("/").text) == ["Shell", "Home"]
    assert root_names(client.get("/bare").text) == ["Bare"]          # layout=None


def test_layout_state_and_module_are_separate_from_the_page(client):
    html = client.get("/").text
    assert '<script id="pw-state-Shell" type="application/json">{"menu":false}</script>' in html
    assert html.index('src="/static/Shell.js') < html.index('src="/static/Home.js')
    assert re.search(r'data-pw-layout="Shell:[0-9a-f]{12}"', html)
    # Static layouts ship no JavaScript but still get a slot.
    users = client.get("/admin/users").text
    assert "Admin.js" not in users and 'data-pw-slot="Admin"' in users


def test_layout_fingerprint_ignores_current_link_marks(client):
    mark = re.compile(r'data-pw-layout="(Shell:[0-9a-f]+)"')
    assert mark.search(client.get("/").text).group(1) == mark.search(client.get("/products").text).group(1)


def test_layout_js_mounts_into_its_slot():
    out = compile_source(APP, filename="app.pyweb")
    js = out["layouts"]["Shell"]["js"]
    assert '$slot("Shell")' in js and '$mount("Shell", Shell);' in js
    assert out["layouts"]["Admin"]["js"] == ""
    assert out["pages"]["Users"]["layouts"] == ["Shell", "Admin"]
    assert out["pages"]["Missing"]["route"] is None and out["pages"]["Missing"]["error_status"] == 404


def test_page_can_choose_a_layout_by_name():
    src = APP.replace('@app.page("/bare", layout=None, noindex=True)', '@app.page("/bare", layout="Admin")')
    out = compile_source(src, filename="app.pyweb")
    assert out["pages"]["Bare"]["layouts"] == ["Shell", "Admin"]


@pytest.mark.parametrize("change, message", [
    (("<main>{children}</main>", "<main></main>"), "layout 'Shell' has no {children}"),
    (("<main>{children}</main>", "<main>{children}{children}</main>"), "uses {children} 2 times"),
    (("def Shell(children):", "def Shell(children, user):"), "can only take `children`"),
    (('@app.layout("/admin")', "@app.layout(3)"), "takes a path prefix"),
    (('layout=None, noindex=True', 'layout="Nope"'), "asks for layout 'Nope', which isn't defined"),
    (("@app.error(404)", "@app.error(200)"), "@app.error takes an HTTP status"),
])
def test_layout_mistakes_are_compile_errors(change, message):
    with pytest.raises(CompileError, match=re.escape(message)):
        compile_source(APP.replace(*change), filename="app.pyweb")


def test_on_unmount_runs_when_the_page_goes_away():
    src = APP.replace("    def inc():\n        count += 1\n",
                      "    def inc():\n        count += 1\n\n    def on_unmount():\n        print(count)\n")
    js = compile_source(src, filename="app.pyweb")["pages"]["Home"]["js"]
    assert "$onCleanup(on_unmount);" in js


# --------------------------------------------------------- error pages

def test_error_pages_are_written_in_pyweb(client):
    resp = client.get("/nope")
    assert resp.status == 404 and '<h1 id="missing">404: nothing at /nope</h1>' in resp.text
    assert root_names(resp.text) == ["Shell", "Missing"]          # root layouts apply
    assert "canonical" not in resp.text


def test_500_page_in_production_and_traceback_in_debug(tmp_path, client):
    from pyweb.hosting import Site
    path = tmp_path / "app.pyweb"
    path.write_text(APP)
    status, _headers, body = Site(str(path)).respond("GET", "/products/9", {}, b"")   # KeyError in the page
    assert status == 500 and b'<h1 id="broken">Sorry (' in body
    resp = client.get("/products/9")                       # TestClient runs in debug mode
    assert resp.status == 500 and "KeyError: 9" in resp.text


def test_a_failing_error_page_falls_back_to_the_built_in_one(tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(APP.replace('<h1 id="missing">{status}: nothing at {path}</h1>',
                                '<h1 id="missing">{1 / 0}</h1>'))
    resp = TestClient(str(path)).get("/nope")
    assert resp.status == 404 and "Page not found" in resp.text


# ---------------------------------------------------------------- head

def test_head_tags_come_from_the_page_head_call_and_app(client):
    html = client.get("/products/2").text
    assert "<title>Desk - Shop</title>" in html
    assert '<meta data-pw-head name="description" content="Buy a Desk">' in html
    assert '<link data-pw-head rel="canonical" href="https://shop.test/products/2">' in html
    assert '<meta data-pw-head property="og:image" content="https://shop.test/static/Desk.png">' in html
    assert '<meta data-pw-head name="twitter:card" content="summary_large_image">' in html
    products = client.get("/products?q=a").text
    assert 'content="All products"' in products and 'href="https://shop.test/products"' in products
    assert 'content="A small shop"' in client.get("/").text
    assert '<meta data-pw-head name="robots" content="noindex">' in client.get("/bare").text


def test_head_outside_a_render_explains_itself():
    from pyweb import head
    with pytest.raises(RuntimeError, match="only works while a page renders"):
        head(title="x")


def test_head_values_are_escaped(tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(APP.replace('description="Buy a " + name', 'description="<script>" + name'))
    html = TestClient(str(path)).get("/products/1").text
    assert 'content="&lt;script&gt;Lamp"' in html


# ---------------------------------------------------- query parameters

def test_query_parameters_are_typed(client):
    text = client.get("/products?q=la&page=3&tags=a&tags=b&sale=yes").text
    assert '<p id="info">la|3|[\'a\', \'b\']|True</p>' in text
    assert '<p id="info">|1|[]|False</p>' in client.get("/products").text
    assert "<li>Lamp</li>" in text and "<li>Desk</li>" not in text


@pytest.mark.parametrize("url, message", [
    ("/products?page=two", "?page=&#x27;two&#x27; isn&#x27;t a valid int"),
    ("/products?sale=maybe", "?sale=&#x27;maybe&#x27; isn&#x27;t a valid bool"),
    ("/search", "missing ?q="),
])
def test_bad_or_missing_query_parameters_are_400(client, url, message):
    resp = client.get(url)
    assert resp.status == 400 and message in resp.text


def test_route_parameters_still_404_when_they_dont_convert(client):
    assert client.get("/products/abc").status == 404


# ------------------------------------------------------- current links

def test_links_to_the_current_page_are_marked(client):
    nav = re.search(r"<nav>.*?</nav>", client.get("/products/1").text).group(0)
    assert '<a aria-current="true" href="/products">Products</a>' in nav     # parent section
    assert '<a aria-current="page" href="/products/1">Lamp</a>' in nav
    assert '<a href="/">Home</a>' in nav
    assert '<a href="/products" aria-current="step">Step</a>' in nav          # the app's own value wins
    assert '<a aria-current="page" href="/">Home</a>' in client.get("/").text


@pytest.mark.parametrize("href, path, expected", [
    ("/a", "/a", "page"), ("/a", "/a/b", "true"), ("/", "/a", None), ("/", "/", "page"),
    ("b", "/a/c", None), ("../a", "/a/c", "true"), ("https://x.test/a", "/a", None), ("#top", "/a", None), ("/ab", "/a", None),
])
def test_link_current(href, path, expected):
    from pyweb.ssr import link_current
    assert link_current(href, path) == expected


# --------------------------------------------------------------- build

def test_build_writes_layout_modules_and_dist_serves_them(tmp_path):
    from pyweb.build import build
    path = tmp_path / "app.pyweb"
    path.write_text(APP)
    out = tmp_path / "dist"
    manifest = build(compile_source(APP, filename=str(path)), str(out), source=APP, app_dir=str(tmp_path))
    shell = manifest["layouts"]["Shell"]["js"]
    assert re.fullmatch(r"Shell\.[0-9a-f]+\.js", shell) and (out / "static" / shell).exists()
    assert manifest["pages"]["Users"]["layouts"] == ["Shell", "Admin"]
    html = TestClient(str(out)).get("/").text
    assert f'src="/static/{shell}"' in html


def test_inspect_lists_layouts_and_error_pages(tmp_path, capsys):
    from pyweb.cli import main
    path = tmp_path / "app.pyweb"
    path.write_text(APP)
    main(["inspect", str(path)])
    out = capsys.readouterr().out
    assert "layout Shell prefix=/ state=['menu']" in out
    assert "page Missing error=404" in out and "layouts=['Shell', 'Admin']" in out


def test_client_nav_can_be_turned_off(tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(APP.replace('description="A small shop")', 'description="A small shop", client_nav=False)'))
    assert '<meta name="pw-nav" content="off">' in TestClient(str(path)).get("/").text


def test_manifest_is_json_serialisable(tmp_path):
    from pyweb.build import build
    manifest = build(compile_source(APP, filename="app.pyweb"), str(tmp_path / "d"), source=APP)
    json.dumps(manifest)


def test_editor_outline_shows_layouts_and_error_pages():
    from pyweb import lsp
    kinds = {s["name"]: s["kind"] for s in lsp.symbols(APP)}
    assert kinds["Shell"] == "layout" and kinds["Missing"] == "error page" and kinds["Home"] == "page"
