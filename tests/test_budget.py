"""Tests for `pyweb build --budget` enforcement."""

from __future__ import annotations

import pytest

from pyweb.cli import _parse_budget

COUNTER = (
    'from pyweb import App\n\napp = App()\n\n@app.page("/")\ndef Home():\n'
    "    count = 0\n\n    def increment():\n        count += 1\n\n"
    "    <main>\n        <button onclick={increment}>\n"
    "            Count: {count}\n        </button>\n    </main>\n"
)


def _run_build(tmp_path, monkeypatch, capsys, budget):
    from pyweb import cli
    src = tmp_path / "app.pyweb"
    src.write_text(COUNTER, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args = type("A", (), {"file": str(src), "out": "dist", "budget": budget})()
    with pytest.raises(SystemExit) as ei:
        cli.cmd_build(args)
    return ei.value.code


def test_build_passes_under_budget(tmp_path, monkeypatch, capsys):
    from pyweb import cli
    src = tmp_path / "app.pyweb"
    src.write_text(COUNTER, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args = type("A", (), {"file": str(src), "out": "dist", "budget": ["static/runtime.js=64KB"]})()
    cli.cmd_build(args)
    assert "built 1 page" in capsys.readouterr().out


def test_build_fails_on_breach(tmp_path, monkeypatch, capsys):
    code = _run_build(tmp_path, monkeypatch, capsys, ["static/runtime.js=1B"])
    assert code == 2
    assert "budget breach" in capsys.readouterr().err


def test_build_total_budget_breach(tmp_path, monkeypatch, capsys):
    code = _run_build(tmp_path, monkeypatch, capsys, ["1B"])
    assert code == 2


def test_parse_budget_units():
    assert _parse_budget("8KB") == 8192
    assert _parse_budget("1B") == 1
    assert _parse_budget("2KB") == 2048
