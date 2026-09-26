"""Tests for `pyweb build --budget` enforcement (Track D)."""
from __future__ import annotations

from pyweb.cli import main


def test_build_passes_under_budget(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["build", "--out", "dist", "--budget", "runtime.js=8KB"]) == 0
    assert "wrote dist" in capsys.readouterr().out


def test_build_fails_on_breach(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = main(["build", "--out", "dist", "--budget", "runtime.js=1B"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "budget breach" in err and "runtime.js" in err


def test_build_total_budget_breach(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["build", "--out", "dist", "--budget", "1B"]) == 2
