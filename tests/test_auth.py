"""/auth — local username/password identity (register, login, me, roles)."""

import pytest
from fastapi.testclient import TestClient

from main import app
from tests.test_analytics import run_chain


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _token(client, username, role="approver"):
    client.post(
        "/auth/register",
        json={"username": username, "password": "password-99", "role": role},
    )
    login = client.post(
        "/auth/login", json={"username": username, "password": "password-99"}
    )
    return login.json()["token"]


def test_approve_derives_operator_identity_from_session(client):
    token = _token(client, "erin")
    body = run_chain(client, service="ec2", occurred_on="2026-07-12", verdict=None)
    action_id = body["action_id"]
    # Body claims a different actor; the server-derived identity must win.
    response = client.post(
        f"/actions/{action_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={"actor": "not-erin", "rationale": "looks right"},
    )
    assert response.status_code == 200
    assert response.json()["decided_by"] == "erin"


def test_decision_without_token_keeps_body_actor(client):
    body = run_chain(client, service="rds", occurred_on="2026-07-12", verdict=None)
    action_id = body["action_id"]
    response = client.post(f"/actions/{action_id}/reject", json={"actor": "cli-bot"})
    assert response.status_code == 200
    assert response.json()["decided_by"] == "cli-bot"


def test_register_login_me_flow(client):
    reg = client.post(
        "/auth/register",
        json={"username": "alice", "password": "s3cret-pw!", "role": "approver"},
    )
    assert reg.status_code == 201
    assert reg.json()["role"] == "approver"
    assert "password" not in reg.json()
    assert "password_hash" not in reg.json()

    login = client.post(
        "/auth/login", json={"username": "alice", "password": "s3cret-pw!"}
    )
    assert login.status_code == 200
    token = login.json()["token"]
    assert token

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "alice"
    assert me.json()["role"] == "approver"


def test_login_rejects_a_bad_password(client):
    client.post("/auth/register", json={"username": "bob", "password": "correct-pw1"})
    bad = client.post(
        "/auth/login", json={"username": "bob", "password": "wrong-pw-123"}
    )
    assert bad.status_code == 401


def test_me_requires_a_valid_token(client):
    assert client.get("/auth/me").status_code == 401
    forged = client.get("/auth/me", headers={"Authorization": "Bearer nope"})
    assert forged.status_code == 401


def test_duplicate_username_conflicts(client):
    client.post("/auth/register", json={"username": "carol", "password": "password-12"})
    dup = client.post(
        "/auth/register", json={"username": "carol", "password": "password-34"}
    )
    assert dup.status_code == 409


def test_invalid_role_is_rejected(client):
    response = client.post(
        "/auth/register",
        json={"username": "dave", "password": "password-12", "role": "root"},
    )
    assert response.status_code == 422
