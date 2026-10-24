"""``pyweb doctor``: each check passes on a healthy project and explains a broken one."""

import json
import os
import socket
import subprocess
import sys

import pytest

from pyweb import doctor

GOOD = "from pyweb import App\napp = App(title='t')\n\n@app.page('/')\ndef Home():\n    <p>hi</p>\n"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("DATABASE_URL", "PYWEB_REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "app.pyweb"
    path.write_text(GOOD)
    (tmp_path / "test_app.py").write_text("def test_x():\n    pass\n")
    return path


def by_name(checks):
    out = {}
    for c in checks:
        out.setdefault(c.name, c)
    return out


def test_a_healthy_app_has_no_problems(app):
    checks = by_name(doctor.run(str(app), port=0))
    assert checks["app"].status == "ok" and "compiles: 1 page" in checks["app"].detail
    assert checks["python"].status == checks["database"].status == checks["tests"].status == "ok"
    assert not [c for c in checks.values() if c.status == "fail"]


def test_a_compile_error_fails_with_the_line_and_a_hint(app):
    app.write_text(GOOD.replace("<p>hi</p>", "<button onclick={lambda: nope()}>x</button>"))
    check = by_name(doctor.run(str(app), port=0))["app"]
    assert check.status == "fail" and "app.pyweb:6" in check.detail and "nope" in check.detail and check.fix


def test_missing_app(tmp_path):
    check = doctor.check_app(str(tmp_path / "nope.pyweb"))
    assert check.status == "fail" and "pyweb new" in check.fix


def test_missing_extras_are_named_with_the_pip_command(app, monkeypatch):
    app.write_text(GOOD.replace("app = App(title='t')", "app = App(title='t', desktop=True)"))
    monkeypatch.setattr(doctor, "_has", lambda mod: mod != "webview")
    [check] = doctor.check_extras(str(app))
    assert check.status == "fail" and "pyweb-stack[desktop]" in check.fix


def test_unreachable_database_fails(app, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:x@127.0.0.1:1/none")
    app.write_text(GOOD.replace("from pyweb import App", "from pyweb import App\nfrom pyweb.models import Model, Field\n\n"
                                "class Note(Model):\n    text: str = Field()\n"))
    check = by_name(doctor.run(str(app), port=0))["database"]
    assert check.status == "fail" and "can't connect to DATABASE_URL" in check.detail


def test_pending_migrations_warn(app):
    app.write_text(GOOD.replace("from pyweb import App", "from pyweb.models import Model, Field\nfrom pyweb import App")
                       .replace("app = App(title='t')", "app = App(title='t', database='sqlite:///app.db')\n\n"
                                "class Note(Model):\n    text: str = Field()\n"))
    res = subprocess.run([sys.executable, "-m", "pyweb.cli", "db", "diff", "--app", "app.pyweb", "--name", "initial"],
                         cwd=app.parent, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
    from pyweb.db import connect
    connect(f"sqlite:///{app.parent / 'app.db'}").execute("SELECT 1")          # the file exists, nothing applied
    checks = by_name(doctor.check_database(str(app)))
    assert checks["migrations"].status == "warn" and "0001_initial" in checks["migrations"].detail
    assert checks["migrations"].fix == "pyweb db upgrade"


def test_secrets_in_source_fail(app):
    (app.parent / "settings.py").write_text('STRIPE = "sk_live_' + "a" * 24 + '"\n')
    [check] = doctor.check_secrets_in_source(str(app))
    assert check.status == "fail" and "environment" in check.fix


def test_stale_dev_link_warns(app):
    from pyweb import devlink
    info = devlink.info_path(str(app))
    os.makedirs(os.path.dirname(info))
    with open(info, "w") as fh:
        json.dump({"url": "http://127.0.0.1:1/", "pid": 999999, "token": "x", "app": str(app)}, fh)
    [check] = doctor.check_dev_link(str(app))
    assert check.status == "warn" and "delete" in check.fix


def test_busy_port_warns():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        check = doctor.check_port(s.getsockname()[1])
    assert check.status == "warn" and "--port" in check.fix


def test_production_problems_fail(app, monkeypatch):
    monkeypatch.delenv("PYWEB_AUTH_SECRET", raising=False)
    checks = [c for c in doctor.run(str(app), production=True, port=0) if c.name == "production"]
    assert checks and all(c.status == "fail" for c in checks)
    assert any("PYWEB_AUTH_SECRET" in c.detail for c in checks)


def test_cli_table_json_and_exit_codes(app):
    run = lambda *a: subprocess.run([sys.executable, "-m", "pyweb.cli", "doctor", *a, "--port", "0"],
                                    cwd=app.parent, capture_output=True, text=True)
    ok = run()
    assert ok.returncode == 0 and "ok    app" in ok.stdout
    data = json.loads(run("--json").stdout)
    assert data["ok"] is True and {"name", "status", "detail", "fix"} <= set(data["checks"][0])
    app.write_text("not markup <")
    bad = run()
    assert bad.returncode == 1 and "FAIL  app" in bad.stdout and "problem(s)" in bad.stdout


def test_mcp_tool(app):
    from pyweb import mcp
    res = mcp.Server().call_tool("pyweb_doctor", {"path": str(app)}, None)
    data = res.get("structuredContent") or json.loads(res["content"][0]["text"])
    assert data["ok"] is True and any(c["name"] == "app" for c in data["checks"])
