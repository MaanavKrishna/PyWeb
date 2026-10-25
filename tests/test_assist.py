"""Fix with AI / Edit with AI (pyweb dev): prompts, secrets, proposals, apply and undo, and the endpoints."""

import json
import os
import textwrap

import pytest

from pyweb import assist

GOOD = textwrap.dedent('''
    from pyweb import App

    app = App(title="Fix me")
    KEY = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"  # a real-looking key


    @app.page("/")
    def Home():
        n = 0
        <main>
            <h1 id="title">Hello</h1>
            <p>{n}</p>
        </main>
''')
BROKEN = GOOD.replace('<h1 id="title">Hello</h1>', '<h1 id="title">Hello</h2>')


def test_secrets_are_replaced_before_sending_and_put_back():
    table = {}
    out = assist.redact(GOOD, table)
    assert "sk-ant-" not in out and "<redacted-1>" in out
    assert assist.unredact(out, table) == GOOD
    assert assist.redact('password = "hunter2hunter2hunter2"', {}) == 'password = "hunter2hunter2hunter2"'  # low entropy


def test_the_file_comes_out_of_the_reply():
    reply = "Here you go:\n```pyweb\nprint(1)\n```\nI fixed the tag."
    assert assist.extract(reply) == "print(1)\n" and assist.summary(reply) == "Here you go:\n\nI fixed the tag."
    assert assist.extract("no fences") == "no fences\n"
    two = "```\nshort\n```\n```python\nthe longer one\n```"
    assert assist.extract(two) == "the longer one\n"


@pytest.fixture
def app(tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(BROKEN)
    return tmp_path, path


def test_apply_writes_keeps_history_and_undo_restores(app):
    folder, path = app
    props = assist.Proposals(str(folder))
    p = props.propose(str(path), GOOD, note="closed the tag")
    assert p["changed"] and not p["problem"] and "-        <h1 id=\"title\">Hello</h2>" in p["diff"]
    assert props.apply(p["id"])["ok"]
    assert path.read_text() == GOOD
    assert list((folder / ".pyweb" / "history").iterdir())[0].read_text() == BROKEN
    assert (folder / ".pyweb" / ".gitignore").read_text() == "*\n"
    props.undo(p["id"])
    assert path.read_text() == BROKEN
    with pytest.raises(assist.AssistError):
        props.undo(p["id"])


def test_a_change_that_does_not_compile_is_refused(app):
    folder, path = app
    props = assist.Proposals(str(folder))
    p = props.propose(str(path), BROKEN.replace("Hello</h2>", "Hello</h3>"))
    assert p["problem"] and "mismatched" in p["problem"]
    with pytest.raises(assist.AssistError, match="doesn't compile"):
        props.apply(p["id"])
    assert path.read_text() == BROKEN


def test_apply_refuses_when_the_file_changed_since(app):
    folder, path = app
    props = assist.Proposals(str(folder))
    p = props.propose(str(path), GOOD)
    path.write_text(BROKEN + "\n# edited by hand\n")
    with pytest.raises(assist.AssistError, match="changed since"):
        props.apply(p["id"])


def test_undo_refuses_after_a_later_edit(app):
    folder, path = app
    props = assist.Proposals(str(folder))
    p = props.propose(str(path), GOOD)
    props.apply(p["id"])
    path.write_text(GOOD + "# more\n")
    with pytest.raises(assist.AssistError, match="by hand"):
        props.undo(p["id"])


def test_only_app_files_are_targets(app, tmp_path_factory):
    folder, path = app
    other = tmp_path_factory.mktemp("other") / "x.pyweb"
    other.write_text("x")
    props = assist.Proposals(str(folder))
    assert props.target("app.pyweb") == os.path.realpath(path)
    for bad in (str(other), "../x.pyweb", None, "app.py"):
        with pytest.raises(assist.AssistError):
            props.target(bad)


# ------------------------------------------------------------------ the dev server's endpoints

LOCAL = {"Host": "127.0.0.1:8000", "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}


@pytest.fixture
def dev(app, monkeypatch):
    from pyweb import cli
    from pyweb.hosting import Site
    folder, path = app
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    state = cli._DevState(str(path))
    cli._dev_load(state, Site)
    assert state.error is not None

    def call(what, data=None, headers=LOCAL, client=("127.0.0.1", 5000), method="POST"):
        status, _h, body = cli._DevSite(state).respond(method, "/__pyweb/dev/ai/" + what, headers,
                                                       json.dumps(data or {}).encode(), client=client)
        return status, json.loads(body)
    return folder, path, state, call


def test_fix_with_a_provider_shows_a_diff_then_applies(dev, monkeypatch, tmp_path):
    folder, path, state, call = dev
    reply = tmp_path / "reply.txt"
    reply.write_text("Closed the h1.\n```pyweb\n" + assist.redact(GOOD, {}) + "```\n")
    monkeypatch.setenv("PYWEB_AI_FAKE", str(reply))
    status, out = call("fix")
    assert status == 200 and out["changed"] and not out["problem"] and out["note"] == "Closed the h1."
    assert out["file"] == "app.pyweb" and "sk-ant-" not in out["diff"]     # the key came back unchanged
    status, done = call("apply", {"id": out["id"]})
    assert status == 200 and path.read_text() == GOOD
    status, _ = call("undo", {"id": out["id"]})
    assert status == 200 and path.read_text() == BROKEN


def test_fix_without_a_provider_gives_a_prompt_and_a_request(dev, monkeypatch):
    folder, path, state, call = dev
    monkeypatch.delenv("PYWEB_AI_FAKE", raising=False)
    assist.PENDING.clear()
    status, out = call("fix")
    assert status == 200 and out["provider"] is None
    assert "mismatched </h2>" in out["prompt"] and "sk-ant-" not in out["prompt"]
    (req,) = assist.PENDING
    assert req["kind"] == "fix" and req["file"] == "app.pyweb" and "sk-ant-" not in req["request"]


def test_edit_targets_the_element_line(dev, monkeypatch, tmp_path):
    folder, path, state, call = dev
    path.write_text(GOOD)
    reply = tmp_path / "reply.txt"
    reply.write_text("```pyweb\n" + GOOD.replace("Hello", "Hello, world") + "```")
    monkeypatch.setenv("PYWEB_AI_FAKE", str(reply))
    line = GOOD.splitlines().index('        <h1 id="title">Hello</h1>') + 1
    status, out = call("edit", {"chain": [f"app.pyweb:{line}"], "instruction": "say hello to the world"})
    assert status == 200 and "+        <h1 id=\"title\">Hello, world</h1>" in out["diff"]
    assert call("edit", {"chain": [f"app.pyweb:{line}"], "instruction": " "})[0] == 400
    assert call("edit", {"chain": ["/etc/passwd:1"], "instruction": "x"})[0] == 400


def test_endpoints_refuse_other_sites_and_other_machines(dev):
    folder, path, state, call = dev
    assert call("fix", headers={**LOCAL, "Sec-Fetch-Site": "cross-site"})[0] == 403
    assert call("fix", headers={"Host": "127.0.0.1:8000", "Origin": "https://evil.example"})[0] == 403
    assert call("fix", client=("192.168.1.9", 5000))[0] == 403
    assert call("fix", headers={**LOCAL, "Host": "abc.trycloudflare.com"})[0] == 403     # through a tunnel
    assert call("status", method="GET")[0] == 200
    assert call("apply", {"id": "nope"})[0] == 409


def test_the_connected_assistant_answers_through_mcp_sampling(app):
    """pyweb dev + pyweb mcp (stdio) + a client offering sampling: Fix with AI is answered by the client."""
    import socket
    import subprocess
    import sys
    import threading
    import time
    import urllib.request
    folder, path = app
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL",
                                                             "PYWEB_AI_FAKE")}
    env.update(PYTHONPATH=root, PYWEB_APP=str(path))
    dev = subprocess.Popen([sys.executable, "-m", "pyweb.cli", "dev", "app.pyweb", "--port", str(port)], cwd=folder,
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    mcp = subprocess.Popen([sys.executable, "-m", "pyweb.cli", "mcp"], cwd=folder, env=env, text=True,
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/__pyweb/dev/ai/"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def ai(what, data=None):
        req = urllib.request.Request(url + what, data=None if data is None else json.dumps(data).encode(),
                                     headers={"Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"})
        with opener.open(req, timeout=60) as r:
            return json.loads(r.read())
    try:
        mcp.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {"sampling": {}},
            "clientInfo": {"name": "test", "version": "1"}}}) + "\n")
        mcp.stdin.flush()
        assert json.loads(mcp.stdout.readline())["id"] == 1
        end = time.time() + 30
        while True:                                  # the MCP side finds the dev server and starts asking for work
            try:
                if ai("status")["provider"] == "your assistant, through pyweb mcp":
                    break
            except OSError:
                pass
            assert time.time() < end, "pyweb mcp never connected to pyweb dev"
            time.sleep(0.2)
        result = {}
        worker = threading.Thread(target=lambda: result.update(ai("fix", {})))
        worker.start()
        msg = json.loads(mcp.stdout.readline())
        assert msg["method"] == "sampling/createMessage"
        sent = msg["params"]["messages"][0]["content"]["text"]
        assert "mismatched </h2>" in sent and "sk-ant-" not in sent and "PyWeb" in msg["params"]["systemPrompt"]
        fixed = sent.split("```pyweb\n", 1)[1].rsplit("```", 1)[0].replace("Hello</h2>", "Hello</h1>")
        mcp.stdin.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "role": "assistant", "model": "test-model",
            "content": {"type": "text", "text": "```pyweb\n" + fixed + "```\nClosed the tag."}}}) + "\n")
        mcp.stdin.flush()
        worker.join(60)
        assert result.get("changed") and not result.get("problem"), result
        assert result["provider"] == "your assistant, through pyweb mcp" and result["note"] == "Closed the tag."
        ai("apply", {"id": result["id"]})
        assert path.read_text() == GOOD                 # the key that was never sent is back in place
    finally:
        mcp.kill()
        dev.terminate()
        dev.wait(10)
        mcp.wait(10)
