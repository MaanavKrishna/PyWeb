"""The 0.6 MCP server: protocol (2025-11-25: titles, hints, output schemas, completions, logging,
progress, resource templates), the new tools, the link to a running `pyweb dev`, and Streamable HTTP."""

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from pyweb import devlink, mcp

ROOT = Path(__file__).parent.parent

APP = textwrap.dedent('''
    import os

    from pyweb import App, Model, platform, server, webhooks
    from pyweb.ui import Button

    app = App(title="Hooks", database="sqlite:///app.db")
    auth = app.use_auth(orgs=True)
    RECEIVED = []


    class Note(Model, tenant=True):
        text: str


    @server(login=True)
    def add_note(text: str) -> dict:
        return Note.create(text=text).to_dict()


    @app.webhook("/hooks/stripe", secret="whsec_test", scheme="stripe")
    def on_stripe(event):
        RECEIVED.append(event.type)


    @app.webhook("/hooks/gh", secret="gh-secret", scheme="github")
    def on_github(event):
        if event.type == "boom":
            raise RuntimeError("handler failed")


    @app.page("/")
    def Home():
        mobile = platform.mobile
        n = 0

        def inc():
            n += 1

        <main>
            <h1>Notes</h1>
            <Button id="inc" onclick={inc}>{n}</Button>
        </main>
''')


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYWEB_WORKER", "0")
    monkeypatch.setattr(mcp, "APPS", mcp._Apps())
    (tmp_path / "app.pyweb").write_text(APP)
    return mcp.Server()


def tool(server, tool_name, **args):
    res = server.call_tool(tool_name, args)
    assert res["isError"] is False, res["content"][0]["text"]
    return res.get("structuredContent", res["content"][0]["text"])


# ------------------------------------------------------------------ protocol

def test_listing_has_titles_hints_and_output_schemas(server):
    init = server.dispatch("initialize", {"protocolVersion": "2025-11-25"})
    assert init["protocolVersion"] == "2025-11-25"
    assert {"tools", "resources", "prompts", "completions", "logging"} <= set(init["capabilities"])
    assert init["serverInfo"]["icons"][0]["src"].startswith("data:image/svg+xml")
    tools = server.dispatch("tools/list", {})["tools"]
    for t in tools:
        assert t["title"] and t["annotations"]["title"] == t["title"], t["name"]
        hints = t["annotations"]
        assert set(hints) >= {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
        assert not (hints["readOnlyHint"] and hints["destructiveHint"])
        for prop, spec in t["inputSchema"].get("properties", {}).items():
            assert "type" in spec or "enum" in spec, (t["name"], prop)
        if t["name"] != "pyweb_guide":                       # the guide is text
            assert t["outputSchema"]["type"] == "object", t["name"]
    by = {t["name"]: t for t in tools}
    assert by["pyweb_docs"]["annotations"]["readOnlyHint"] and by["pyweb_packages"]["annotations"]["openWorldHint"]
    assert len(by) == len(tools)                              # names are unique


def test_arguments_are_checked_before_the_tool_runs(server):
    bad = server.call_tool("pyweb_explain", {"line": "3", "nope": 1})
    text = bad["content"][0]["text"]
    assert bad["isError"] and "'line' should be integer" in text and "unknown argument 'nope'" in text
    assert "missing required argument 'line'" in server.call_tool("pyweb_explain", {})["content"][0]["text"]


def test_resources_templates_and_completions(server):
    uris = [r["uri"] for r in server.dispatch("resources/list", {})["resources"]]
    assert {"pyweb://docs", "pyweb://kit", "pyweb://docs/teams", "pyweb://threat-model"} <= set(uris)
    templates = [t["uriTemplate"] for t in server.dispatch("resources/templates/list", {})["resourceTemplates"]]
    assert templates == ["pyweb://docs/{page}", "pyweb://kit/{component}", "pyweb://templates/{name}"]
    read = server.dispatch("resources/read", {"uri": "pyweb://docs/teams"})["contents"][0]
    assert read["text"].startswith("# Teams") and read["mimeType"] == "text/markdown"
    kit = server.dispatch("resources/read", {"uri": "pyweb://kit/Button"})["contents"][0]["text"]
    assert "from pyweb.ui import Button" in kit and "`outline`" in kit and "def Button(" in kit
    assert "pyweb://kit/Dialog" in server.dispatch("resources/read", {"uri": "pyweb://kit"})["contents"][0]["text"]
    done = server.dispatch("completion/complete", {"ref": {"type": "ref/resource", "uri": "pyweb://docs/{page}"},
                                                   "argument": {"name": "page", "value": "tea"}})["completion"]
    assert done["values"] == ["teams"]
    paths = server.dispatch("completion/complete", {"ref": {"type": "ref/prompt", "name": "add_teams"},
                                                    "argument": {"name": "path", "value": ""}})["completion"]
    assert paths["values"] == ["app.pyweb"]
    with pytest.raises(Exception):
        mcp.read_resource("pyweb://docs/no-such-page")


def test_prompts_render_and_require_their_arguments(server):
    prompts = server.dispatch("prompts/list", {})["prompts"]
    names = [p["name"] for p in prompts]
    assert {"add_teams", "make_installable", "make_desktop_app", "security_review", "upgrade_pyweb", "design_pass",
            "fix_failing_test", "debug_page"} <= set(names)
    for p in prompts:
        args = {a["name"]: "x" for a in p.get("arguments", [])}
        got = server.dispatch("prompts/get", {"name": p["name"], "arguments": args})
        text = got["messages"][0]["content"]["text"]
        assert "Steps:" in text and "pyweb_" in text, p["name"]
        for word in __import__("re").findall(r"\bpyweb_[a-z_]+", text):
            assert word in {t["name"] for t in mcp.TOOLS}, (p["name"], word)     # prompts name real tools
    resp = server.handle({"jsonrpc": "2.0", "id": 1, "method": "prompts/get", "params": {"name": "debug_page"}})
    assert resp["error"]["code"] == -32602 and "url" in resp["error"]["message"]


def test_progress_and_log_notifications(server):
    sent = []
    server._send = sent.append
    assert server.dispatch("logging/setLevel", {"level": "debug"}) == {}
    reset = mcp._CALL.set({"server": server, "token": "tok"})
    try:
        mcp.progress(1, 4, "one")
        mcp.log("debug", "hello", n=1)
    finally:
        mcp._CALL.reset(reset)
    assert sent[0] == {"jsonrpc": "2.0", "method": "notifications/progress",
                       "params": {"progressToken": "tok", "progress": 1, "total": 4, "message": "one"}}
    assert sent[1]["params"] == {"level": "debug", "logger": "pyweb", "data": {"message": "hello", "n": 1}}
    server.dispatch("logging/setLevel", {"level": "error"})
    reset = mcp._CALL.set({"server": server, "token": None})
    try:
        mcp.progress(2)                 # no token: nothing
        mcp.log("info", "quiet")        # below the level: nothing
    finally:
        mcp._CALL.reset(reset)
    assert len(sent) == 2
    # A real tool call reports progress when asked (pyweb_profile, every fifth run).
    server.call_tool("pyweb_profile", {"repeat": 6}, token=7)
    assert any(m["method"] == "notifications/progress" and m["params"]["progressToken"] == 7 for m in sent)


def test_output_matches_the_schema(server):
    jsonschema = pytest.importorskip("jsonschema")
    by = {t["name"]: t for t in mcp.TOOLS}
    for name, args in [("pyweb_check", {}), ("pyweb_routes", {}), ("pyweb_docs", {"query": "webhook"}),
                       ("pyweb_ui", {}), ("pyweb_ui", {"action": "show", "component": "Card"}),
                       ("pyweb_upgrade", {}), ("pyweb_deploy_plan", {}), ("pyweb_teams", {}),
                       ("pyweb_explain", {"line": 17}), ("pyweb_dev", {}), ("pyweb_logs", {}),
                       ("pyweb_webhook_send", {"hook": "/hooks/stripe"}), ("pyweb_profile", {"repeat": 2})]:
        jsonschema.validate(tool(server, name, **args), by[name]["output"])


# ------------------------------------------------------------------ tools

def test_docs_search_and_pages(server):
    found = tool(server, "pyweb_docs", query="invite members roles")
    assert found["results"][0]["page"] == "teams"
    assert found["results"][0]["uri"] == "pyweb://docs/teams"
    page = tool(server, "pyweb_docs", page="desktop", max_chars=500)
    assert page["text"].startswith("# Desktop") and page["truncated"] is True
    assert tool(server, "pyweb_docs", query="DNS rebinding Host desktop")["results"][0]["page"] in ("threat-model", "desktop")
    assert "teams" in [p["page"] for p in tool(server, "pyweb_docs")["pages"]]


def test_ui_list_show_add_and_theme(server, tmp_path):
    families = tool(server, "pyweb_ui")["families"]
    button = next(c for f in families for c in f["components"] if c["name"] == "Button")
    assert "outline" in button["values"]["variant"]
    shown = tool(server, "pyweb_ui", action="show", component="Dialog")
    assert shown["import"] == "from pyweb.ui import Dialog" and "def Dialog(" in shown["source"]
    added = tool(server, "pyweb_ui", action="add", component="Button")["written"][0]
    assert added["import"].startswith("from components.button import Button")
    assert (tmp_path / "components" / "button.pyweb").exists()
    theme = tool(server, "pyweb_ui", action="theme", preset="rose", radius=4, save=True)
    assert theme["code"] == 'Theme(accent="#e11d48", radius=4)' and theme["accent_text_contrast"] >= 4.5
    assert 'theme=Theme(accent="#e11d48", radius=4)' in (tmp_path / "app.pyweb").read_text()
    assert server.call_tool("pyweb_ui", {"action": "show", "component": "Nope"})["isError"]


def test_upgrade_and_deploy_plan(server):
    up = tool(server, "pyweb_upgrade")
    assert any(f["code"] == "platform-placeholder" for f in up["findings"])
    plan = tool(server, "pyweb_deploy_plan", target="fly")
    assert "postgres" in plan["provision"] and plan["processes"]["worker"] is True
    assert "Deploy plan for" in plan["plan"]


def test_webhook_send_signs_runs_and_reports(server, tmp_path):
    ok = tool(server, "pyweb_webhook_send", hook="/hooks/stripe", event="invoice.paid", data={"amount": 5})
    assert ok["status"] == 202 and ok["jobs_ran"] == 1 and ok["job"]["state"] == "done"
    assert mcp.APPS.client(str(tmp_path / "app.pyweb")).app.module.RECEIVED == ["invoice.paid"]
    forged = tool(server, "pyweb_webhook_send", hook="/hooks/stripe", bad_signature=True)
    assert forged["status"] == 401
    failed = tool(server, "pyweb_webhook_send", hook="/hooks/gh", event="boom")
    assert failed["status"] == 202 and "handler failed" in failed["job"]["error"]
    assert "pass hook" in server.call_tool("pyweb_webhook_send", {})["content"][0]["text"]


def test_profile_explain_and_teams(server):
    prof = tool(server, "pyweb_profile", url="/", repeat=3)
    assert prof["mean_ms"] > 0 and prof["top"] and all("mcp_tools" not in r["where"] for r in prof["top"])
    assert any(r["where"].startswith("app.pyweb:") for r in prof["app_code"])
    explained = tool(server, "pyweb_explain", line=17, name="add_note")
    assert "Runs on the server" in explained["names"]["add_note"]
    inside = tool(server, "pyweb_explain", line=37, name="inc")
    assert "browser" in inside["names"]["inc"]
    tool(server, "pyweb_call", function="add_note", args={"text": "hi"}, as_user="a@x.com", org="Acme")
    teams = tool(server, "pyweb_teams")
    acme = teams["orgs"][0]
    assert acme["name"] == "Acme" and acme["members"] == [{"email": "a@x.com", "role": "owner"}]
    assert acme["rows"] == {"Note": 1} and teams["tenant_models"] == ["Note"]


# ------------------------------------------------------------------ the running dev server

def test_dev_link_needs_its_token():
    class State:
        path, site, error, version = "app.pyweb", None, ValueError("bad line"), 3
    link = devlink.Link(State())
    try:
        assert link.handle("GET", "/__pyweb/dev/agent/status", {})[0] == 403
        assert link.handle("GET", "/__pyweb/dev/agent/status", {devlink.HEADER: "nope"})[0] == 403
        ok = link.handle("GET", "/__pyweb/dev/agent/status", {devlink.HEADER: link.token}, "127.0.0.1")
        body = json.loads(ok[2])
        assert ok[0] == 200 and body["compiles"] is False and "bad line" in body["error"]["message"]
        far = link.handle("GET", "/__pyweb/dev/agent/status", {devlink.HEADER: link.token}, "10.0.0.5")
        assert far[0] == 403
        assert link.handle("GET", "/other", {}) is None
    finally:
        __import__("logging").getLogger().removeHandler(link.logs)


def test_tools_see_a_running_pyweb_dev(server, tmp_path):
    env = {**os.environ, "PYWEB_WORKER": "0", "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen([sys.executable, "-m", "pyweb.cli", "dev", "app.pyweb", "--port", "0"], cwd=tmp_path,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    info_file = tmp_path / ".pyweb" / "dev.json"
    try:
        for _ in range(200):
            if info_file.exists():
                break
            time.sleep(0.05)
        info = json.loads(info_file.read_text())
        assert oct(info_file.stat().st_mode & 0o777) == "0o600"
        assert (tmp_path / ".pyweb" / ".gitignore").read_text() == "*\n"
        dev = tool(server, "pyweb_dev")
        assert dev["running"] and dev["compiles"] and dev["url"] == info["url"]
        assert [p["route"] for p in dev["pages"]] == ["/"]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        opener.open(info["url"]).read()                                  # a browser visit
        reqs = tool(server, "pyweb_requests", source="dev")
        assert reqs["target"] == "dev" and reqs["requests"][-1]["path"] == "/"
        with pytest.raises(urllib.error.HTTPError) as no_token:          # a web page can't read it
            opener.open(info["url"] + "__pyweb/dev/agent/status")
        assert no_token.value.code == 403
        (tmp_path / "app.pyweb").write_text(APP.replace("<h1>Notes</h1>", "<h1>Notes"))     # broken
        for _ in range(100):
            dev = tool(server, "pyweb_dev")
            if not dev["compiles"]:
                break
            time.sleep(0.1)
        assert dev["compiles"] is False and "mismatched" in dev["error"]["message"]
        logs = tool(server, "pyweb_logs", level="debug")
        assert logs["running"] and isinstance(logs["logs"], list)
    finally:
        proc.terminate()
        proc.wait(10)
    assert not info_file.exists()                                         # removed on exit
    assert tool(server, "pyweb_dev")["running"] is False


# ------------------------------------------------------------------ Streamable HTTP

@pytest.fixture
def http_url():
    from pyweb.mcp_http import make_server
    srv = make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/mcp"
    srv.shutdown()
    srv.server_close()


def _post(url, msg, sid=None, headers=None, accept="application/json, text/event-stream"):
    h = {"Content-Type": "application/json", "Accept": accept, **(headers or {})}
    if sid:
        h["Mcp-Session-Id"] = sid
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url, json.dumps(msg).encode(), h)) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode()


def test_http_sessions_streams_and_checks(http_url, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.pyweb").write_text(APP)
    status, headers, body = _post(http_url, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                             "params": {"protocolVersion": "2025-06-18"}})
    sid = headers["Mcp-Session-Id"]
    assert status == 200 and json.loads(body)["result"]["protocolVersion"] == "2025-06-18"
    assert _post(http_url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)[0] == 202
    status, _h, body = _post(http_url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, sid)
    assert status == 200 and len(json.loads(body)["result"]["tools"]) == len(mcp.TOOLS)
    # A tool call with a progress token streams the notifications, then the result.
    status, headers, body = _post(http_url, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "pyweb_profile", "arguments": {"repeat": 6}, "_meta": {"progressToken": "p"}}}, sid)
    events = [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]
    assert headers["Content-Type"] == "text/event-stream"
    assert events[0]["method"] == "notifications/progress" and events[-1]["id"] == 3
    assert events[-1]["result"]["isError"] is False
    # Checks: no session, unknown session, a foreign page, a bad protocol version.
    assert _post(http_url, {"jsonrpc": "2.0", "id": 4, "method": "ping"})[0] == 400
    assert _post(http_url, {"jsonrpc": "2.0", "id": 4, "method": "ping"}, "nope")[0] == 404
    assert _post(http_url, {"jsonrpc": "2.0", "id": 4, "method": "ping"}, sid,
                 {"Origin": "https://evil.example"})[0] == 403
    assert _post(http_url, {"jsonrpc": "2.0", "id": 4, "method": "ping"}, sid,
                 {"MCP-Protocol-Version": "1999-01-01"})[0] == 400
    assert _post(http_url, {"jsonrpc": "2.0", "id": 4, "method": "ping"}, sid,
                 {"Origin": "http://localhost:3000"})[0] == 200
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with pytest.raises(urllib.error.HTTPError) as get:
        opener.open(http_url)
    assert get.value.code == 405
    end = urllib.request.Request(http_url, method="DELETE", headers={"Mcp-Session-Id": sid})
    assert opener.open(end).status == 200
    assert _post(http_url, {"jsonrpc": "2.0", "id": 5, "method": "ping"}, sid)[0] == 404


def test_http_needs_a_token_beyond_this_machine(monkeypatch):
    from pyweb.mcp_http import make_server
    monkeypatch.delenv("PYWEB_MCP_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        make_server("0.0.0.0", 0)
    srv = make_server("127.0.0.1", 0, token="s3cret")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/mcp"
    try:
        msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        assert _post(url, msg)[0] == 401
        assert _post(url, msg, headers={"Authorization": "Bearer s3cret"})[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()


def test_official_sdk_over_http(http_url, tmp_path, monkeypatch):
    pytest.importorskip("mcp.client.streamable_http")
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client as streamablehttp_client
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.pyweb").write_text(APP)

    async def run():
        async with streamablehttp_client(http_url) as streams:       # (read, write[, session id]) by SDK version
            async with ClientSession(streams[0], streams[1]) as s:
                init = await s.initialize()
                tools = (await s.list_tools()).tools
                res = await s.call_tool("pyweb_routes", {})          # validated against its output schema
                doc = await s.read_resource("pyweb://docs/teams")
                def pick(obj, camel, snake):                       # SDK 1.x camelCase, 2.x snake_case
                    return getattr(obj, camel, None) if hasattr(obj, camel) else getattr(obj, snake)
                return (pick(init, "protocolVersion", "protocol_version"), len(tools),
                        pick(res, "structuredContent", "structured_content"), doc.contents[0].text)

    version, count, routes, doc = asyncio.run(run())
    assert version == mcp.PROTOCOL_VERSIONS[0] and count == len(mcp.TOOLS)
    assert routes["pages"][0]["route"] == "/" and doc.startswith("# Teams")
