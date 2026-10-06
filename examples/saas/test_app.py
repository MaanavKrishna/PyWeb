"""Tests for Teamboard. Run them with `pytest` (or the pyweb_test MCP tool)."""

from pathlib import Path

import pytest

from pyweb import RPCError, jobs
from pyweb.testing import TestClient

APP = str(Path(__file__).parent / "app.pyweb")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the SQLite file goes here, not in the project
    monkeypatch.setenv("PYWEB_WORKER", "0")  # tests run jobs themselves (below)
    return TestClient(APP)


def test_signed_out_visitors_see_the_landing_page(client):
    assert "Plan the week" in client.get("/").text
    assert client.get("/projects").status == 303  # to /login


def test_create_a_project_and_its_checklist(client):
    client.login("ann@example.com")
    project = client.rpc("create_project", project={"name": "Website"})
    assert jobs.Worker(schedule=False).drain() == 1  # the welcome_tasks job
    page = client.get(f"/projects/{project['id']}")
    assert page.status == 200 and "Invite your team" in page.text


def test_forms_check_their_fields(client):
    client.login("ann@example.com")
    with pytest.raises(RPCError) as err:
        client.rpc("create_project", project={"name": "x"})
    assert err.value.code == "validation_error"


def test_people_only_see_their_own_projects(client):
    client.login("ann@example.com")
    project = client.rpc("create_project", project={"name": "Secret plans"})
    client.logout()
    client.login("bob@example.com")
    assert client.get(f"/projects/{project['id']}").status == 404
    with pytest.raises(RPCError):
        client.rpc("add_task", project_id=project["id"], title="sneaky")


def test_tasks(client):
    client.login("ann@example.com")
    project = client.rpc("create_project", project={"name": "Chores"})
    task = client.rpc("add_task", project_id=project["id"], title="Water plants")
    assert client.rpc("toggle", task_id=task["id"]) is True
    assert "Water plants" in client.get(f"/projects/{project['id']}").text
