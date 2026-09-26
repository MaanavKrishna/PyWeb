"""Tests for `pyweb inspect` JSON output and ASCII tree (Track D)."""
from __future__ import annotations

import json

import pytest

from pyweb._graph import discover_app_graph
from pyweb.cli import _print_tree, main


@pytest.fixture()
def sample_app(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text(
        'from pyweb import route, signal\n'
        '\n'
        '@app.route("/")\n'
        'def index():\n'
        '    return "hi"\n'
        '\n'
        'class Card(Component):\n'
        '    title: str\n'
        '    count = 0\n'
        '\n'
        'class Shell(ServerComponent):\n'
        '    pass\n'
        '\n'
        'count = signal(0)\n'
        '\n'
        '@app.rpc\n'
        'def save():\n'
        '    return True\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_inspect_json_has_edges_and_reasons(sample_app, capsys):
    assert main(["inspect", "--root", ".", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["routes"] and data["routes"][0]["path"] == "/"
    names = {c["name"] for c in data["components"]}
    assert {"Card", "Shell"} <= names
    for c in data["components"]:
        assert "decision" in c["placement"] and "reason" in c["placement"]
        assert c["placement"]["reason"], "placement must carry a reason"
    assert {s["name"] for s in data["signals"]} == {"count"}
    assert [r["name"] for r in data["rpcs"]] == ["save"]
    assert data["rpc_edges"] and data["rpc_edges"][0]["to"] == "save"


def test_inspect_tree_shows_reasons(sample_app, capsys):
    assert main(["inspect", "--root", "."]) == 0
    out = capsys.readouterr().out
    assert "routes (1)" in out and "components (2)" in out
    assert "ServerComponent base renders on the server" in out


def test_inspect_consumes_placement_output(sample_app, monkeypatch, capsys):
    import sys
    import types

    mod = types.ModuleType("placement")
    mod.decide = lambda graph: {
        c["name"]: {"decision": "island", "reason": "uses signal count"}
        for c in graph["components"]
    }
    monkeypatch.setitem(sys.modules, "placement", mod)
    assert main(["inspect", "--root", ".", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {c["placement"]["decision"] for c in data["components"]} == {"island"}
    assert all("uses signal" in c["placement"]["reason"] for c in data["components"])


def test_inspect_tree_unit():
    from pyweb._graph import AppGraph, ComponentInfo, PlacementInfo, RouteInfo

    g = AppGraph(root=".", routes=[RouteInfo("/", "index", "app.py", 3)],
                 components=[ComponentInfo("Card", "shared", "app.py", 6, ["title"],
                                           PlacementInfo("shared", "default"))])
    tree = _print_tree(g)
    assert "app (.)" in tree and "Card" in tree and "default" in tree
