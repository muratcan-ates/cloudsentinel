"""Tests for the HITL action lifecycle endpoints (WP-5a).

Acceptance criteria from the sprint plan: approve/reject endpoints green,
state-transition rules enforced, and double-POST safety proven both
sequentially and concurrently (Idempotency-Key replay).
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import db
from main import app


@pytest.fixture
def client():
    # conftest points SENTINEL_DB_PATH at a per-test throwaway file; the
    # lifespan (entered by the context manager) builds the schema there.
    with TestClient(app) as test_client:
        yield test_client


def seed_action(state: str = "proposed") -> int:
    """Insert one event + action directly, the way WP-4 eventually will."""
    conn = db.connect()
    try:
        with db.writing(conn):
            event_id = db.upsert_event(
                conn,
                kind="cost_anomaly",
                service="ec2",
                occurred_on="2026-07-12",
                payload_json="{}",
            )
            cursor = conn.execute(
                "INSERT INTO actions (event_id, title, detail_json, state) "
                "VALUES (?, 'scale down idle instances', ?, ?)",
                (event_id, json.dumps({"risk": "low"}), state),
            )
            return cursor.lastrowid
    finally:
        conn.close()


# --- listing -----------------------------------------------------------------


def test_list_actions_empty(client):
    response = client.get("/actions")
    assert response.status_code == 200
    assert response.json() == {"count": 0, "actions": []}


def test_list_actions_returns_seeded_action(client):
    action_id = seed_action()
    body = client.get("/actions").json()
    assert body["count"] == 1
    record = body["actions"][0]
    assert record["id"] == action_id
    assert record["state"] == "proposed"
    assert record["detail"] == {"risk": "low"}
    assert record["decided_at"] is None


def test_list_actions_state_filter(client):
    seed_action(state="proposed")
    seed_action(state="approved")
    proposed = client.get("/actions", params={"state": "proposed"}).json()
    approved = client.get("/actions", params={"state": "approved"}).json()
    assert proposed["count"] == 1
    assert approved["count"] == 1
    assert proposed["actions"][0]["state"] == "proposed"


def test_list_actions_rejects_unknown_state(client):
    assert client.get("/actions", params={"state": "sideways"}).status_code == 422


# --- state transitions --------------------------------------------------------


def test_approve_proposed_action(client):
    action_id = seed_action()
    response = client.post(f"/actions/{action_id}/approve")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "approved"
    assert body["decided_by"] == "operator"
    assert body["decided_at"] is not None
    assert body["executed_at"] is None


def test_reject_proposed_action_with_actor(client):
    action_id = seed_action()
    response = client.post(
        f"/actions/{action_id}/reject", json={"actor": "tuana"}
    )
    assert response.status_code == 200
    assert response.json()["state"] == "rejected"
    assert response.json()["decided_by"] == "tuana"


def test_decide_unknown_action_is_404(client):
    assert client.post("/actions/999/approve").status_code == 404


def test_out_of_range_action_id_is_422(client):
    """Ids beyond SQLite's signed 64-bit range must fail validation, not 500."""
    huge = 2**63  # one past the largest storable id
    assert client.post(f"/actions/{huge}/approve").status_code == 422
    assert client.post("/actions/0/reject").status_code == 422


def test_whitespace_actor_is_rejected(client):
    """A blank-after-strip actor would put an empty hand in the audit trail."""
    action_id = seed_action()
    response = client.post(f"/actions/{action_id}/approve", json={"actor": "   "})
    assert response.status_code == 422


def test_actor_is_stripped_before_recording(client):
    action_id = seed_action()
    response = client.post(f"/actions/{action_id}/approve", json={"actor": "  tuana  "})
    assert response.status_code == 200
    assert response.json()["decided_by"] == "tuana"


def test_openapi_documents_decision_conflicts(client):
    """The 404/409 state-machine contract must be visible in /docs."""
    paths = client.get("/openapi.json").json()["paths"]
    for verb in ("approve", "reject"):
        responses = paths[f"/actions/{{action_id}}/{verb}"]["post"]["responses"]
        assert "404" in responses
        assert "409" in responses


def test_double_decision_without_key_conflicts(client):
    action_id = seed_action()
    assert client.post(f"/actions/{action_id}/approve").status_code == 200
    second = client.post(f"/actions/{action_id}/approve")
    assert second.status_code == 409
    assert "'approved'" in second.json()["detail"]


def test_reject_after_approve_conflicts(client):
    action_id = seed_action()
    client.post(f"/actions/{action_id}/approve")
    assert client.post(f"/actions/{action_id}/reject").status_code == 409


def test_empty_actor_is_rejected(client):
    action_id = seed_action()
    response = client.post(f"/actions/{action_id}/approve", json={"actor": ""})
    assert response.status_code == 422


def test_decision_survives_in_database(client):
    action_id = seed_action()
    client.post(f"/actions/{action_id}/approve")
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT state, decided_by FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["state"] == "approved"
    assert row["decided_by"] == "operator"


# --- idempotency: sequential ---------------------------------------------------


@pytest.mark.parametrize("verb", ["approve", "reject"])
def test_idempotency_key_replays_first_response(client, verb):
    """Both decision endpoints must wire the header, not just approve."""
    action_id = seed_action()
    headers = {"Idempotency-Key": "click-abc"}
    first = client.post(f"/actions/{action_id}/{verb}", headers=headers)
    second = client.post(f"/actions/{action_id}/{verb}", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    # the replay really came from the idempotency store, not a re-decision
    conn = db.connect()
    try:
        stored = conn.execute("SELECT count(*) FROM idempotency").fetchone()[0]
    finally:
        conn.close()
    assert stored == 1


def test_idempotency_key_is_scoped_per_verb(client):
    """The same client key on reject must not replay the approve response."""
    action_id = seed_action()
    headers = {"Idempotency-Key": "click-abc"}
    assert (
        client.post(f"/actions/{action_id}/approve", headers=headers).status_code
        == 200
    )
    # different verb, same key: no replay, so the state machine answers 409
    assert (
        client.post(f"/actions/{action_id}/reject", headers=headers).status_code
        == 409
    )


def test_idempotency_key_is_scoped_per_action(client):
    """The same client key on another action must decide that action."""
    first_id = seed_action()
    second_id = seed_action()
    headers = {"Idempotency-Key": "click-abc"}
    client.post(f"/actions/{first_id}/approve", headers=headers)
    response = client.post(f"/actions/{second_id}/approve", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == second_id


def test_failed_decision_is_not_stored_for_replay(client):
    """A 409 must roll back the claim so the key stays reusable."""
    action_id = seed_action(state="approved")
    headers = {"Idempotency-Key": "click-late"}
    assert (
        client.post(f"/actions/{action_id}/approve", headers=headers).status_code
        == 409
    )
    # the rollback must reach the storage layer: no claim row may survive,
    # otherwise a mutant that commits claims on failure would pass here
    conn = db.connect()
    try:
        leftover = conn.execute("SELECT count(*) FROM idempotency").fetchone()[0]
    finally:
        conn.close()
    assert leftover == 0
    # and the key stays reusable: same 409, not a bogus replay
    assert (
        client.post(f"/actions/{action_id}/approve", headers=headers).status_code
        == 409
    )


# --- idempotency: concurrent ----------------------------------------------------


def _race_two_posts(post):
    """Fire two POSTs that are provably in flight at the same time.

    An outside connection holds the write lock (BEGIN IMMEDIATE) while both
    requests are submitted, so both are queued inside their transactions
    before either can decide — the race is forced, not probabilistic.
    Without this, fast serialized requests would just repeat the
    sequential tests and a claim-in-separate-transaction mutant would
    slip through.
    """
    holder = db.connect()
    holder.execute("BEGIN IMMEDIATE")
    try:
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(post) for _ in range(2)]
            time.sleep(0.5)  # both requests are now parked on the write lock
            assert not any(f.done() for f in futures), "requests did not overlap"
            holder.commit()  # release: the two decisions race through the queue
            return [f.result() for f in futures]
    finally:
        holder.close()


def test_concurrent_double_post_same_key(client):
    """Two racing clicks with one key: both get the same 200 response."""
    action_id = seed_action()
    headers = {"Idempotency-Key": "race-1"}
    responses = _race_two_posts(
        lambda: client.post(f"/actions/{action_id}/approve", headers=headers)
    )

    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    conn = db.connect()
    try:
        decided = conn.execute(
            "SELECT count(*) FROM actions WHERE state = 'approved'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert decided == 1


def test_concurrent_double_post_without_key(client):
    """Two racing clicks without a key: exactly one wins, one conflicts."""
    action_id = seed_action()
    responses = _race_two_posts(
        lambda: client.post(f"/actions/{action_id}/approve")
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
