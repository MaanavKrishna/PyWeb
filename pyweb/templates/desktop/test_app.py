"""Tests for Notes. Run them with `pytest` (or the pyweb_test MCP tool)."""

from pathlib import Path

import pytest

from pyweb import RPCError
from pyweb.testing import TestClient

APP = str(Path(__file__).parent / "app.pyweb")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the SQLite file goes here, not in the project
    return TestClient(APP)


def test_write_and_list_notes(client):
    assert "Make a note with New" in client.get("/").text
    note = client.rpc("new_note")
    client.rpc("save_note", note_id=note["id"], title="Groceries", body="- eggs\n- *milk*")
    page = client.get("/").text
    assert "Groceries" in page and "<em>milk</em>" in page      # the Markdown preview


def test_blank_titles_become_untitled(client):
    note = client.rpc("new_note")
    assert client.rpc("save_note", note_id=note["id"], title="  ", body="")["title"] == "Untitled"


def test_delete(client):
    note = client.rpc("new_note")
    client.rpc("delete_note", note_id=note["id"])
    with pytest.raises(RPCError):
        client.rpc("delete_note", note_id=note["id"])                 # already gone


def test_export_asks_where_to_save(client, monkeypatch, tmp_path):
    from pyweb import desktop
    note = client.rpc("new_note")
    client.rpc("save_note", note_id=note["id"], title="Trip plan", body="Pack light.")
    saved = {}

    def save_file(filename, data, types=()):          # stands in for the system's Save dialog
        saved.update(filename=filename, data=data)
        return str(tmp_path / filename)

    monkeypatch.setattr(desktop, "save_file", save_file)
    assert client.rpc("export_note", note_id=note["id"]).endswith("trip-plan.md")
    assert saved["data"] == "# Trip plan\n\nPack light.\n"


def test_export_needs_the_desktop_app(client):
    note = client.rpc("new_note")
    with pytest.raises(RPCError):                     # in a browser there is no Save dialog
        client.rpc("export_note", note_id=note["id"])
    assert 'id="export"' not in client.get("/").text   # so the button isn't shown there
