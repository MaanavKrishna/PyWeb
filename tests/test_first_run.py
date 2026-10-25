"""The first five minutes: `pyweb new` asks in a terminal, `pyweb dev` shows where to go and welcomes you once."""

import io
import json
import os
import sys
import types

import pytest

from pyweb import cli


class _TTY(io.StringIO):
    def isatty(self):
        return True


def _args(**kw):
    return types.SimpleNamespace(**{"name": None, "template": None, "theme": None, **kw})


def test_new_asks_for_name_template_and_colours(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    answers = iter(["shop", "3", "rose"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(sys, "stdin", _TTY())
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    cli.cmd_new(_args())
    out = capsys.readouterr().out
    assert "todo" in out and "a list you add to" in out and "rose" in out
    source = (tmp_path / "shop" / "app.pyweb").read_text()
    assert 'theme=Theme(accent="#e11d48")' in source and "from pyweb.ui import" in source
    from pyweb.compiler import compile_source
    compile_source(source, filename=str(tmp_path / "shop" / "app.pyweb"))


def test_new_defaults_and_bad_answers(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    answers = iter(["", "99", "nope", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(sys, "stdin", _TTY())
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    cli.cmd_new(_args())
    assert "pick a number" in capsys.readouterr().out
    assert "theme=" not in (tmp_path / "my-app" / "app.pyweb").read_text()     # counter, default colours


def test_new_without_a_terminal_needs_a_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    with pytest.raises(SystemExit, match="give the app a name"):
        cli.cmd_new(_args())
    cli.cmd_new(_args(name="plain", theme="emerald"))
    assert 'accent="#059669"' in (tmp_path / "plain" / "app.pyweb").read_text()


def test_announce_prints_the_network_url_and_a_qr_code(monkeypatch):
    monkeypatch.setattr(cli, "_lan_address", lambda: "192.168.1.20")
    opened = []
    monkeypatch.setattr(cli, "_can_open_browser", lambda: True)
    monkeypatch.setattr("webbrowser.open", opened.append)
    out = _TTY()
    cli._announce("0.0.0.0", 8000, "http://localhost:8000/", open_browser=False, out=out)
    text = out.getvalue()
    assert "On your network: http://192.168.1.20:8000/" in text and "▀" in text
    out = _TTY()
    cli._announce("127.0.0.1", 8000, "http://localhost:8000/", open_browser=False, out=out)
    assert "--host 0.0.0.0" in out.getvalue() and "▀" not in out.getvalue()
    plain = io.StringIO()
    cli._announce("0.0.0.0", 8000, "http://localhost:8000/", open_browser=False, out=plain)
    assert "▀" not in plain.getvalue()                  # no QR code into a file or a pipe


def test_the_browser_opens_only_on_a_desktop(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    for k in ("CI", "SSH_CONNECTION", "SSH_TTY", "PYWEB_NO_OPEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert cli._can_open_browser()
    monkeypatch.setenv("CI", "true")
    assert not cli._can_open_browser()
    monkeypatch.delenv("CI")
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 5 6.7.8.9 22")
    assert not cli._can_open_browser()


def test_welcome_shows_until_dismissed(tmp_path):
    from pyweb.hosting import Site
    path = tmp_path / "app.pyweb"
    path.write_text('from pyweb import App\napp = App()\n\n@app.page("/")\ndef Home():\n    <p>hi</p>\n')
    state = cli._DevState(str(path))
    cli._dev_load(state, Site)
    state.welcome = True
    site = cli._DevSite(state)
    local = {"Host": "127.0.0.1:8000"}

    def page(headers=local, client=("127.0.0.1", 1)):
        return site.respond("GET", "/", headers, b"", client=client)[2].decode()
    assert "__PYWEB_WELCOME__={" in page()
    assert "__PYWEB_WELCOME__={" not in page({"Host": "x.trycloudflare.com"})      # not for someone else
    status, _h, body = site.respond("POST", "/__pyweb/dev/welcome", {**local, "Sec-Fetch-Site": "cross-site"}, b"",
                                    client=("127.0.0.1", 1))
    assert status == 403
    status, _h, body = site.respond("POST", "/__pyweb/dev/welcome", {**local, "Sec-Fetch-Site": "same-origin"}, b"",
                                    client=("127.0.0.1", 1))
    assert status == 200 and json.loads(body)["ok"]
    assert "__PYWEB_WELCOME__={" not in page()
    assert os.path.exists(tmp_path / ".pyweb" / "welcomed")


def test_the_cli_knows_every_theme_preset_without_importing_the_kit():
    from pyweb._templates import THEME_PRESETS
    from pyweb.ui.theme import PRESETS
    assert list(THEME_PRESETS) == list(PRESETS)
    import subprocess
    out = subprocess.run([sys.executable, "-X", "importtime", "-m", "pyweb.cli", "--version"], capture_output=True,
                         text=True, check=True).stderr
    assert "pyweb.ui.theme" not in out and "pyweb.share" not in out and "pyweb.qr" not in out
