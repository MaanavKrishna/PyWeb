"""MCP server: protocol, tools, scaffolding, and interop with the official SDK."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pyweb import mcp

ROOT = Path(__file__).parent.parent


def call(server, method, params=None, mid=1):
    resp = server.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
    assert "error" not in resp, resp
    return resp["result"]


def tool(server, name, **args):
    res = call(server, "tools/call", {"name": name, "arguments": args})
    assert res["isError"] is False, res
    return res.get("structuredContent", res["content"][0]["text"])


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return mcp.Server()


def test_initialize_and_listing(server):
    init = call(server, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                       "clientInfo": {"name": "t", "version": "1"}})
    assert init["protocolVersion"] == "2025-03-26"
    assert init["serverInfo"]["name"] == "pyweb" and "pyweb_guide" in init["instructions"]
    assert call(server, "initialize", {"protocolVersion": "1999-01-01"})["protocolVersion"] == mcp.PROTOCOL_VERSIONS[0]
    names = [t["name"] for t in call(server, "tools/list")["tools"]]
    assert names == ["pyweb_guide", "pyweb_new_app", "pyweb_check", "pyweb_inspect",
                     "pyweb_compiled", "pyweb_render", "pyweb_call", "pyweb_routes", "pyweb_packages",
                     "pyweb_screenshot", "pyweb_test"]
    uris = [r["uri"] for r in call(server, "resources/list")["resources"]]
    assert "pyweb://guide" in uris and "pyweb://templates/todo" in uris
    assert call(server, "prompts/list")["prompts"][0]["name"] == "build_pyweb_app"
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    bad = server.handle({"jsonrpc": "2.0", "id": 9, "method": "nope"})
    assert bad["error"]["code"] == -32601


def test_build_check_render_call_loop(server, tmp_path):
    created = tool(server, "pyweb_new_app", directory="shop", template="blog", title="Shop")["created"]
    assert {os.path.basename(f) for f in created} >= {"app.pyweb", "test_app.py", "AGENTS.md", "CLAUDE.md", "app.css"}
    assert 'App(title="Shop"' in (tmp_path / "shop" / "app.pyweb").read_text()
    assert (tmp_path / "shop" / "CLAUDE.md").read_text() == "@AGENTS.md\n"

    check = tool(server, "pyweb_check", path="shop/app.pyweb")
    assert check["ok"] and [p["name"] for p in check["pages"]] == ["Home", "Post"]
    assert check["server_functions"][0]["name"] == "publish"

    err = tool(server, "pyweb_call", path="shop/app.pyweb", function="publish", args={"title": "", "body": ""})
    assert err == {"ok": False, "error": {"code": "validation_error",
                                          "message": "A post needs a title and a body.", "status": 422}}
    ok = tool(server, "pyweb_call", path="shop/app.pyweb", function="publish", args={"title": "T", "body": "B"})
    assert ok["ok"] and ok["result"][0]["title"] == "T"
    page = tool(server, "pyweb_render", path="shop/app.pyweb", url="/posts/1")
    assert page["status"] == 200 and "<h1>T</h1>" in page["body"]
    assert tool(server, "pyweb_render", path="shop/app.pyweb", url="/posts/2")["status"] == 404

    placement = tool(server, "pyweb_inspect", path="shop/app.pyweb")["pages"]["Home"]["placement"]
    assert placement["posts"]["runs"] == "browser" and placement["submit"]["runs"] == "browser"
    js = tool(server, "pyweb_compiled", path="shop/app.pyweb", page="Home", what="js")["javascript"]
    assert '$rpc("publish"' in js


def test_check_reports_line_and_hint(server):
    src = ('from pyweb import App\nimport sqlite3\napp = App()\n@app.page("/")\ndef H():\n'
           '    n = 0\n    def go():\n        sqlite3.connect("x")\n    <button onclick={go}>{n}</button>\n')
    res = tool(server, "pyweb_check", source=src)
    assert res["ok"] is False
    assert res["errors"][0]["line"] == 8
    assert "@server" in res["errors"][0]["hint"]
    markup = tool(server, "pyweb_check", source='from pyweb import App\napp = App()\n@app.page("/")\ndef H():\n    <p>x</div>\n')
    assert markup["errors"][0]["line"] == 5 and "hint" in markup["errors"][0]


def test_render_reports_python_traceback(server, tmp_path):
    (tmp_path / "app.pyweb").write_text('from pyweb import App\napp = App()\n@app.page("/")\ndef H():\n'
                                        '    x = 1 // 0\n    <p>{x}</p>\n')
    res = tool(server, "pyweb_render")
    assert res["status"] == 500 and "ZeroDivisionError" in res["error"]


def test_session_persists_between_call_and_render(server, tmp_path):
    (tmp_path / "app.pyweb").write_text(
        'from pyweb import App, redirect, server, session\napp = App()\n\n@server\ndef login(name: str) -> bool:\n'
        '    session.login(name)\n    return True\n\n@app.page("/")\ndef H():\n    user = session.user()\n'
        '    if not user:\n        return redirect("/login")\n    who = user["sub"]\n    <p>hi {who}</p>\n')
    assert tool(server, "pyweb_render")["redirect_to"] == "/login"
    tool(server, "pyweb_call", function="login", args={"name": "ada"})
    assert "<p>hi ada</p>" in tool(server, "pyweb_render")["body"]


def test_guide_sections_and_tool_errors(server):
    assert tool(server, "pyweb_guide", section="markup").startswith("## 4. Markup")
    res = call(server, "tools/call", {"name": "pyweb_check", "arguments": {"path": "missing.pyweb"}})
    assert res["structuredContent"]["ok"] is False
    boom = call(server, "tools/call", {"name": "pyweb_new_app", "arguments": {"template": "nope"}})
    assert boom["isError"] is True and "unknown template" in boom["content"][0]["text"]


def test_every_pyweb_block_in_the_guide_compiles():
    import re
    from pyweb.compiler import compile_source
    text = (ROOT / "pyweb" / "ai" / "guide.md").read_text()
    blocks = re.findall(r"```pyweb\n(.*?)```", text, re.S)
    assert len(blocks) >= 2
    for b in blocks:
        compile_source(b)


@pytest.mark.parametrize("name", ["counter", "todo", "blog", "auth", "chat", "ai-chat"])
def test_templates_match_examples(name):
    packaged = (ROOT / "pyweb" / "templates" / f"{name}.pyweb").read_text()
    assert packaged == (ROOT / "examples" / name / "app.pyweb").read_text()


def test_template_css_matches_examples():
    assert (ROOT / "pyweb" / "templates" / "app.css").read_text() == \
        (ROOT / "examples" / "todo" / "static" / "app.css").read_text()


def test_stdio_transport(tmp_path):
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "pyweb_new_app", "arguments": {"directory": "x", "template": "counter"}}}]
    proc = subprocess.run([sys.executable, "-m", "pyweb.cli", "mcp"], cwd=tmp_path, capture_output=True, text=True,
                          input="\n".join(json.dumps(m) for m in msgs) + "\nnot json\n", timeout=60)
    lines = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [line.get("id") for line in lines] == [1, 2, None]
    assert lines[2]["error"]["code"] == -32700
    assert (tmp_path / "x" / "app.pyweb").exists()


def test_official_sdk_client(tmp_path):
    sdk = pytest.importorskip("mcp.client.stdio")
    from mcp import ClientSession, StdioServerParameters

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "pyweb.cli", "mcp"], cwd=str(tmp_path))
        async with sdk.stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = [t.name for t in (await s.list_tools()).tools]
                res = await s.call_tool("pyweb_check", {"source": 'from pyweb import App\napp = App()\n'
                                                                  '@app.page("/")\ndef H():\n    <p>hi</p>\n'})
                return names, res.content[0].text

    names, text = asyncio.run(run())
    assert "pyweb_check" in names and json.loads(text)["ok"] is True


@pytest.mark.parametrize("template", ["counter", "blog", "auth"])
def test_new_apps_come_with_a_passing_test(server, tmp_path, template):
    tool(server, "pyweb_new_app", directory="app", template=template)
    res = tool(server, "pyweb_test", path="app")
    assert res["ok"] and res["passed"] == 1 and res["failed"] == 0, res["output"]
    assert not (tmp_path / "app" / "blog.db").exists()  # the starter test keeps app files out of the project


def test_pyweb_test_reports_failures_and_missing_tests(server, tmp_path):
    tool(server, "pyweb_new_app", directory="app", template="counter")
    (tmp_path / "app" / "test_more.py").write_text(
        "from pyweb.testing import TestClient\n\n"
        "def test_title():\n    assert 'Nope' in TestClient('app.pyweb').get('/').text\n")
    res = tool(server, "pyweb_test", path="app")
    assert res["ok"] is False and res["passed"] == 1 and res["failed"] == 1
    assert "test_title" in res["output"] and "1 failed, 1 passed" in res["summary"]
    assert tool(server, "pyweb_test", path="app", filter="home")["passed"] == 1
    (tmp_path / "empty").mkdir()
    none = tool(server, "pyweb_test", path="empty")
    assert none["ok"] is False and "No tests found" in none["hint"]


# ------------------------------------------------------------ 0.4 features

def site_app(tmp_path):
    from tests.test_multipage import APP
    (tmp_path / "app.pyweb").write_text(APP)
    return "app.pyweb"


def test_routes_map_pages_layouts_and_parameters(server, tmp_path):
    routes = tool(server, "pyweb_routes", path=site_app(tmp_path))
    pages = {p["page"]: p for p in routes["pages"]}
    assert pages["Products"]["route"] == "/products" and pages["Products"]["layouts"] == ["Shell"]
    assert pages["Products"]["params"][1] == {"name": "page", "type": "int", "from": "query", "default": "1"}
    assert pages["Product"]["params"] == [{"name": "pid", "type": "int", "from": "path"}]
    assert pages["Search"]["params"][0]["required"] is True
    assert pages["Missing"]["error_status"] == 404 and pages["Bare"]["head"] == {"noindex": True}
    assert {lay["layout"]: lay["pages"] for lay in routes["layouts"]}["Admin"] == ["Users"]


def test_render_reports_title_head_and_roots(server, tmp_path):
    res = tool(server, "pyweb_render", path=site_app(tmp_path), url="/products/2")
    assert res["title"] == "Desk - Shop" and res["roots"] == ["Shell", "Product"]
    assert res["head"]["description"] == "Buy a Desk" and res["head"]["canonical"] == "https://shop.test/products/2"


def test_call_returns_streamed_chunks(server, tmp_path):
    (tmp_path / "app.pyweb").write_text("from pyweb import App, server\napp = App()\n\n\n@server\n"
                                        "def count(n: int):\n    for i in range(n):\n        yield i\n\n\n"
                                        "@app.page('/')\ndef H():\n    <p>hi</p>\n")
    assert tool(server, "pyweb_call", function="count", args={"n": 3}) == {
        "ok": True, "streamed": True, "chunks": [0, 1, 2], "cookies": []}
    check = tool(server, "pyweb_check")
    assert check["server_functions"] == [{"name": "count", "args": [{"name": "n", "type": "int"}],
                                          "returns": "Any", "streams": True}]


def test_packages_add_list_remove(server, tmp_path, monkeypatch):
    from pyweb import packages
    from tests.test_packages import registry
    fake = registry()
    monkeypatch.setattr(packages, "Registry", lambda *a, **k: fake)
    (tmp_path / "app.pyweb").write_text("from pyweb import App\napp = App()\n@app.page('/')\ndef H():\n    <p/>\n")
    added = tool(server, "pyweb_packages", add=["gauge-widget"])
    assert added["ok"] and added["packages"]["gauge-widget"]["version"] == "1.1.0"
    assert "Gauge" in added["packages"]["gauge-widget"]["exports"]
    assert added["usage"] == ['X = npm("gauge-widget")  # default export; npm("gauge-widget", "Name") for a named '
                              'one, "*" for all']
    assert tool(server, "pyweb_packages")["specifiers"] == ["gauge-widget", "tiny-color"]
    assert tool(server, "pyweb_packages", remove=["gauge-widget"])["packages"] == {}
    bad = tool(server, "pyweb_packages", add=["old-cjs"])
    assert bad["ok"] is False and "CommonJS" in bad["error"] and "lodash-es" in bad["hint"]


@pytest.mark.parametrize("name, words", [("add_ai_feature", ["yield", "<Markdown", ".cancel()"]),
                                         ("make_data_live", ["live(db", "db.notify"]),
                                         ("build_pyweb_app", ["pyweb_routes", "pyweb_packages"])])
def test_prompts(server, name, words):
    assert name in [p["name"] for p in call(server, "prompts/list")["prompts"]]
    text = call(server, "prompts/get", {"name": name, "arguments": {"feature": "summaries",
                                                                    "description": "a shop"}})
    body = text["messages"][0]["content"]["text"]
    assert all(w in body for w in words), body


def test_guide_has_the_0_4_sections(server):
    for section, title in [("streaming", "## 10. Streaming"), ("live data", "## 11. Live data"),
                           ("npm", "## 12. npm packages"), ("multi-page", "## 9. Multi-page")]:
        assert tool(server, "pyweb_guide", section=section).startswith(title)
