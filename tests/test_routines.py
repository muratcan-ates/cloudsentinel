"""/routines — saved, named, read-only analysis playbooks (CRUD + run)."""

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_routine_crud_and_run(client):
    created = client.post(
        "/routines",
        json={
            "name": "Morning check",
            "description": "daily ritual",
            "steps": ["insights", "pending_actions", "cost_summary"],
        },
    )
    assert created.status_code == 201
    routine = created.json()
    routine_id = routine["id"]
    assert routine["steps"] == ["insights", "pending_actions", "cost_summary"]

    assert client.get("/routines").json()["count"] == 1

    fetched = client.get(f"/routines/{routine_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Morning check"

    run = client.post(f"/routines/{routine_id}/run")
    assert run.status_code == 200
    body = run.json()
    assert body["routine"] == "Morning check"
    assert {step["step"] for step in body["steps"]} == {
        "insights",
        "pending_actions",
        "cost_summary",
    }
    cost = next(step for step in body["steps"] if step["step"] == "cost_summary")
    assert cost["summary"]["total"] > 0

    assert client.delete(f"/routines/{routine_id}").status_code == 204
    assert client.get("/routines").json()["count"] == 0


def test_create_rejects_unknown_step(client):
    response = client.post(
        "/routines", json={"name": "bad", "steps": ["insights", "launch_missiles"]}
    )
    assert response.status_code == 422


def test_run_and_get_404_for_unknown_routine(client):
    assert client.get("/routines/999999").status_code == 404
    assert client.post("/routines/999999/run").status_code == 404
