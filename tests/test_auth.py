"""/auth — local username/password identity (register, login, me, roles)."""

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


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
