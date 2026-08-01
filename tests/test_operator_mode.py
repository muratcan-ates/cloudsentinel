"""Operator authorization mode (``SENTINEL_REQUIRE_APPROVER``) + bootstrap.

Acceptance criteria:
- flag off → decisions behave exactly as today (anonymous approve still OK);
- flag on + no or invalid token → 401 on all three decision verbs, and
  nothing moves in the state machine; reads stay public;
- flag on + an authenticated viewer → 403 naming the required role —
  registration stays open, so strangers can register but cannot decide;
- flag on + the env-bootstrapped admin (``SENTINEL_ADMIN_USER`` /
  ``SENTINEL_ADMIN_PASSWORD``) → login works and approve succeeds with the
  server-derived identity;
- the bootstrap is idempotent across boots and never resets the password;
- ``SENTINEL_READONLY`` still wins when both flags are set (403 before auth).
"""

import pytest
from fastapi.testclient import TestClient

from main import app
from tests.test_actions import seed_action


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _viewer_token(client, username):
    client.post(
        "/auth/register",
        json={"username": username, "password": "password-99"},
    )
    login = client.post(
        "/auth/login", json={"username": username, "password": "password-99"}
    )
    return login.json()["token"]


def test_flag_off_keeps_anonymous_decisions(client):
    action_id = seed_action()
    response = client.post(f"/actions/{action_id}/approve")
    assert response.status_code == 200
    assert response.json()["decided_by"] == "operator"


def test_flag_on_requires_a_session_on_all_three_verbs(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_REQUIRE_APPROVER", "1")
    approve_id = seed_action()
    reject_id = seed_action()
    execute_id = seed_action(state="approved")
    assert client.post(f"/actions/{approve_id}/approve").status_code == 401
    assert client.post(f"/actions/{reject_id}/reject").status_code == 401
    assert client.post(f"/actions/{execute_id}/execute").status_code == 401
    forged = {"Authorization": "Bearer not-a-real-token"}
    assert (
        client.post(f"/actions/{approve_id}/approve", headers=forged).status_code
        == 401
    )
    # nothing moved, and the inbox itself stays publicly readable
    inbox = client.get("/actions")
    assert inbox.status_code == 200
    states = {a["id"]: a["state"] for a in inbox.json()["actions"]}
    assert states[approve_id] == "proposed"
    assert states[execute_id] == "approved"


def test_registration_stays_open_but_viewers_cannot_decide(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_REQUIRE_APPROVER", "1")
    # a stranger can still self-register — but only ever as a viewer
    token = _viewer_token(client, "stranger")
    action_id = seed_action()
    response = client.post(
        f"/actions/{action_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "viewer" in response.json()["detail"]
    assert "approver" in response.json()["detail"]


def test_bootstrap_admin_logs_in_and_decides(monkeypatch):
    monkeypatch.setenv("SENTINEL_REQUIRE_APPROVER", "1")
    monkeypatch.setenv("SENTINEL_ADMIN_USER", "ops-lead")
    monkeypatch.setenv("SENTINEL_ADMIN_PASSWORD", "landing-strip-77")
    with TestClient(app) as client:  # fresh context: the lifespan bootstraps
        login = client.post(
            "/auth/login",
            json={"username": "ops-lead", "password": "landing-strip-77"},
        )
        assert login.status_code == 200
        assert login.json()["user"]["role"] == "admin"
        token = login.json()["token"]
        action_id = seed_action()
        response = client.post(
            f"/actions/{action_id}/approve",
            headers={"Authorization": f"Bearer {token}"},
            json={"actor": "ignored-hand", "rationale": "bootstrapped decision"},
        )
        assert response.status_code == 200
        # the server-derived identity wins over the request body, as always
        assert response.json()["decided_by"] == "ops-lead"


def test_bootstrap_is_idempotent_and_never_resets_the_password(monkeypatch):
    monkeypatch.setenv("SENTINEL_ADMIN_USER", "ops-lead")
    monkeypatch.setenv("SENTINEL_ADMIN_PASSWORD", "first-password-1")
    with TestClient(app):
        pass  # first boot creates the account
    monkeypatch.setenv("SENTINEL_ADMIN_PASSWORD", "changed-password-2")
    with TestClient(app) as client:  # second boot: same user, new env password
        original = client.post(
            "/auth/login",
            json={"username": "ops-lead", "password": "first-password-1"},
        )
        assert original.status_code == 200  # the stored hash was not touched
        changed = client.post(
            "/auth/login",
            json={"username": "ops-lead", "password": "changed-password-2"},
        )
        assert changed.status_code == 401


def test_readonly_still_wins_over_operator_mode(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_READONLY", "1")
    monkeypatch.setenv("SENTINEL_REQUIRE_APPROVER", "1")
    # 403 with the read-only detail, not 401: the showcase guard answers
    # before auth is ever consulted
    response = client.post("/actions/1/approve")
    assert response.status_code == 403
    assert "read-only" in response.json()["detail"]
