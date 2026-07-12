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
    for verb in ("approve", "reject", "execute"):
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


# --- simulated execution (WP-5b) ------------------------------------------------


def test_execute_approved_action(client):
    action_id = seed_action(state="approved")
    response = client.post(f"/actions/{action_id}/execute")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "executed"
    assert body["executed_at"] is not None
    assert body["detail"]["execution"]["mode"] == "SIMULATION"


@pytest.mark.parametrize("state", ["proposed", "rejected", "executed"])
def test_execute_requires_approved_state(client, state):
    action_id = seed_action(state=state)
    response = client.post(f"/actions/{action_id}/execute")
    assert response.status_code == 409
    assert f"'{state}'" in response.json()["detail"]


def test_execute_unknown_action_is_404(client):
    assert client.post("/actions/999/execute").status_code == 404


def test_execute_out_of_range_id_is_422(client):
    assert client.post(f"/actions/{2**63}/execute").status_code == 422


def test_execute_replays_with_idempotency_key(client):
    action_id = seed_action(state="approved")
    headers = {"Idempotency-Key": "run-once"}
    first = client.post(f"/actions/{action_id}/execute", headers=headers)
    second = client.post(f"/actions/{action_id}/execute", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    # without the key the state machine answers 409 (already executed)
    assert client.post(f"/actions/{action_id}/execute").status_code == 409


def test_execute_claim_rolls_back_on_conflict(client):
    """A 409 must not burn the execute idempotency key."""
    action_id = seed_action()  # still proposed: execute conflicts
    headers = {"Idempotency-Key": "exec-early"}
    assert client.post(f"/actions/{action_id}/execute", headers=headers).status_code == 409
    conn = db.connect()
    try:
        leftover = conn.execute("SELECT count(*) FROM idempotency").fetchone()[0]
    finally:
        conn.close()
    assert leftover == 0
    # once approved, the SAME key must execute normally (not replay the 409)
    client.post(f"/actions/{action_id}/approve")
    response = client.post(f"/actions/{action_id}/execute", headers=headers)
    assert response.status_code == 200
    assert response.json()["state"] == "executed"


def test_concurrent_execute_same_key(client):
    action_id = seed_action(state="approved")
    headers = {"Idempotency-Key": "exec-race"}
    responses = _race_two_posts(
        lambda: client.post(f"/actions/{action_id}/execute", headers=headers)
    )
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()


def test_concurrent_execute_without_key(client):
    action_id = seed_action(state="approved")
    responses = _race_two_posts(
        lambda: client.post(f"/actions/{action_id}/execute")
    )
    assert sorted(r.status_code for r in responses) == [200, 409]


def test_idempotency_key_is_scoped_per_verb_across_the_lifecycle(client):
    """The same client key on approve and then execute must not replay the
    approve response — verbs are separate idempotency scopes."""
    action_id = seed_action()
    headers = {"Idempotency-Key": "same-key"}
    approve = client.post(f"/actions/{action_id}/approve", headers=headers)
    assert approve.json()["state"] == "approved"
    execute = client.post(f"/actions/{action_id}/execute", headers=headers)
    assert execute.status_code == 200
    assert execute.json()["state"] == "executed"  # fresh transition, no replay


def test_full_lifecycle_proposed_to_executed(client):
    action_id = seed_action()
    assert client.post(f"/actions/{action_id}/approve").json()["state"] == "approved"
    body = client.post(f"/actions/{action_id}/execute").json()
    assert body["state"] == "executed"
    assert body["decided_at"] is not None  # the approval audit survived
    assert body["detail"]["execution"]["mode"] == "SIMULATION"


# --- request-triggered expiry (WP-5b) ---------------------------------------------


def seed_stale_proposal(hours_old: float, state: str = "proposed") -> int:
    conn = db.connect()
    try:
        with db.writing(conn):
            event_id = db.upsert_event(
                conn,
                kind="cost_anomaly",
                service="stale-svc",
                occurred_on="2026-07-01",
                payload_json="{}",
            )
            cursor = conn.execute(
                "INSERT INTO actions (event_id, title, detail_json, state, proposed_at) "
                "VALUES (?, 'stale proposal', '{}', ?, datetime('now', ?))",
                (event_id, state, f"-{hours_old} hours"),
            )
            return cursor.lastrowid
    finally:
        conn.close()


def test_stale_proposals_expire_when_the_inbox_is_read(client):
    stale_id = seed_stale_proposal(hours_old=100)  # default TTL is 72h
    fresh_id = seed_action()
    body = client.get("/actions").json()
    by_id = {a["id"]: a for a in body["actions"]}
    assert by_id[stale_id]["state"] == "rejected"
    assert by_id[stale_id]["decided_by"] == "system:timeout"
    assert by_id[stale_id]["decided_at"] is not None
    assert by_id[fresh_id]["state"] == "proposed"  # untouched


def test_expiry_ttl_env_override(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_ACTION_TTL_HOURS", "1")
    stale_id = seed_stale_proposal(hours_old=2)
    body = client.get("/actions").json()
    assert body["actions"][0]["id"] == stale_id
    assert body["actions"][0]["state"] == "rejected"


def test_expiry_disabled_with_zero_ttl(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_ACTION_TTL_HOURS", "0")
    stale_id = seed_stale_proposal(hours_old=1000)
    body = client.get("/actions").json()
    assert body["actions"][0]["id"] == stale_id
    assert body["actions"][0]["state"] == "proposed"  # expiry off


def test_expired_action_cannot_be_decided(client):
    stale_id = seed_stale_proposal(hours_old=100)
    client.get("/actions")  # triggers the expiry sweep
    assert client.post(f"/actions/{stale_id}/approve").status_code == 409


def test_expiry_only_sweeps_proposed_actions(client):
    """Old approved/executed actions are settled history, not stale
    proposals — the sweep must never touch them."""
    approved_id = seed_stale_proposal(hours_old=100, state="approved")
    executed_id = seed_stale_proposal(hours_old=100, state="executed")
    by_id = {a["id"]: a for a in client.get("/actions").json()["actions"]}
    assert by_id[approved_id]["state"] == "approved"
    assert by_id[executed_id]["state"] == "executed"


def test_ttl_garbage_and_nonfinite_values_fall_back_to_default(monkeypatch):
    from app import actions

    for garbage in ("abc", "nan", "inf", "-inf", "infinity", "+inf"):
        monkeypatch.setenv("SENTINEL_ACTION_TTL_HOURS", garbage)
        assert actions.action_ttl_hours() == actions.DEFAULT_ACTION_TTL_HOURS
    monkeypatch.setenv("SENTINEL_ACTION_TTL_HOURS", "1.5")
    assert actions.action_ttl_hours() == 1.5


def test_nan_ttl_does_not_silently_disable_expiry(client, monkeypatch):
    """A non-finite TTL must behave like garbage (default 72h), not like a
    permanent, silent off-switch for the timeout safety net."""
    monkeypatch.setenv("SENTINEL_ACTION_TTL_HOURS", "nan")
    stale_id = seed_stale_proposal(hours_old=1000)
    body = client.get("/actions").json()
    assert body["actions"][0]["id"] == stale_id
    assert body["actions"][0]["state"] == "rejected"


# --- CORS (WP-5b) -------------------------------------------------------------------


def test_cors_preflight_allows_the_idempotency_key_header(client):
    response = client.options(
        "/actions/1/approve",
        headers={
            "Origin": "https://cloudsentinel.onrender.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "idempotency-key",
        },
    )
    assert response.status_code == 200
    assert (
        response.headers["access-control-allow-origin"]
        == "https://cloudsentinel.onrender.com"
    )
    assert "idempotency-key" in response.headers["access-control-allow-headers"].lower()


def test_cors_actual_responses_carry_the_allowed_origin(client):
    """Preflight is not enough: the real responses must also be readable
    cross-origin, and the CSV filename header must be exposed."""
    origin = {"Origin": "https://cloudsentinel.onrender.com"}
    response = client.get("/actions", headers=origin)
    assert (
        response.headers["access-control-allow-origin"]
        == "https://cloudsentinel.onrender.com"
    )
    export = client.get("/costs/summary/export", headers=origin)
    exposed = export.headers.get("access-control-expose-headers", "").lower()
    assert "content-disposition" in exposed


def test_cors_rejects_unknown_origins(client):
    response = client.options(
        "/actions/1/approve",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in response.headers


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
