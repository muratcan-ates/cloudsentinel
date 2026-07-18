"""Tests for the cross-lane HITL pack: fraud-hold cards, the budget
guard, confidence calibration, the headline, the searchable decision
ledger, the TTL badge and the debate-overturn counter.
"""

import pytest
from fastapi.testclient import TestClient

from app import db
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# --- fraud holds in the decision box ---------------------------------------------


def test_fraud_hold_cards_ride_the_same_lifecycle(client):
    report = client.post("/pulse").json()
    assert report["fraud_holds_filed"] == 2
    inbox = client.get("/actions").json()["actions"]
    card = next(a for a in inbox if a["detail"].get("kind") == "fraud_hold")
    assert "hold suggested" in card["title"]
    assert card["detail"]["fraud"]["band"] == "hold_suggested"

    # the operator decides it exactly like a cost card — same state machine
    decided = client.post(
        f"/actions/{card['id']}/approve",
        json={"actor": "op", "rationale": "velocity pattern confirmed by the customer"},
    )
    assert decided.status_code == 200
    assert decided.json()["state"] == "approved"

    # the verdict lands in the ledger under the transaction id
    ledger = client.get(
        "/decisions", params={"q": "velocity pattern"}
    ).json()
    assert ledger["count"] == 1
    assert ledger["decisions"][0]["service"] == card["detail"]["fraud"]["id"]


def test_fraud_holds_never_inflate_the_cost_funnel(client):
    client.post("/pulse")
    funnel = client.get("/analytics/decisions").json()["funnel"]
    # 2 cost signals -> 2 cost proposals; the 2 fraud holds stay out
    assert funnel["proposals"] == funnel["signals"]


def test_rejected_fraud_hold_is_refiled_on_a_later_sweep(client):
    report = client.post("/pulse").json()
    assert report["fraud_holds_filed"] == 2
    card = next(
        a for a in client.get("/actions").json()["actions"]
        if a["detail"].get("kind") == "fraud_hold"
    )
    tx_id = card["detail"]["fraud"]["id"]

    rejected = client.post(
        f"/actions/{card['id']}/reject",
        json={"actor": "op", "rationale": "false alarm — the customer verified it"},
    )
    assert rejected.status_code == 200

    # reuse guard is state != 'rejected': the rejected card re-files, the
    # still-open second hold is reused, so exactly one card is filed anew.
    resweep = client.post("/pulse").json()
    assert resweep["fraud_holds_filed"] == 1
    fresh = [
        a for a in client.get("/actions").json()["actions"]
        if a["detail"].get("kind") == "fraud_hold"
        and a["detail"]["fraud"]["id"] == tx_id
        and a["state"] == "proposed"
    ]
    assert fresh, "the rejected fraud hold should re-file as a fresh proposed card"


# --- budget guard ----------------------------------------------------------------


def test_budget_guard_files_one_card_per_month(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_MONTHLY_BUDGET", "100")  # far below spend
    first = client.post("/pulse").json()
    assert first["budget_cards_filed"] == 1
    second = client.post("/pulse").json()
    assert second["budget_cards_filed"] == 0  # open card reused
    card = next(
        a for a in client.get("/actions").json()["actions"]
        if a["detail"].get("kind") == "budget_risk"
    )
    assert card["detail"]["overage"] > 0
    assert {o["stance"] for o in card["detail"]["options"]} == {"CAUTIOUS", "BOLD"}
    assert "automatically" in card["detail"]["note"]


def test_budget_guard_is_inert_without_the_knob(client):
    report = client.post("/pulse").json()
    assert report["budget_cards_filed"] == 0
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT count(*) FROM events WHERE kind = 'budget_risk'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert rows == 0


def test_rejected_budget_guard_card_is_refiled(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_MONTHLY_BUDGET", "100")  # far below spend
    assert client.post("/pulse").json()["budget_cards_filed"] == 1
    card = next(
        a for a in client.get("/actions").json()["actions"]
        if a["detail"].get("kind") == "budget_risk"
    )
    rejected = client.post(
        f"/actions/{card['id']}/reject",
        json={"actor": "op", "rationale": "seasonal spike, expected this month"},
    )
    assert rejected.status_code == 200
    # same reuse semantics as the fraud lane: a rejected card re-files.
    assert client.post("/pulse").json()["budget_cards_filed"] == 1


# --- calibration -----------------------------------------------------------------


def test_calibration_buckets_confidence_against_verdicts(client):
    conn = db.connect()
    try:
        with db.writing(conn):
            for confidence, verdict in ((0.5, "approved"), (0.5, "rejected"),
                                        (0.9, "approved"), (None, "approved")):
                context = (
                    f'{{"confidence": {{"score": {confidence}}}}}'
                    if confidence is not None
                    else '{"origin": "seeded"}'
                )
                conn.execute(
                    "INSERT INTO decisions (action_id, service, verdict, rationale, "
                    "input_context_json) VALUES (NULL, 'compute', ?, NULL, ?)",
                    (verdict, context),
                )
    finally:
        conn.close()
    body = client.get("/analytics/calibration").json()
    assert body["decisions_with_confidence"] == 3  # the no-confidence row is excluded
    by_range = {bucket["range"]: bucket for bucket in body["buckets"]}
    assert by_range["0.4–0.6"]["decisions"] == 2
    assert by_range["0.4–0.6"]["approval_rate"] == 0.5
    assert by_range["0.8–1.0"]["approval_rate"] == 1.0
    assert by_range["0.0–0.4"]["approval_rate"] is None


# --- headline, search, ttl badge, overturn counter -------------------------------


def test_headline_composes_the_funnel_story(client):
    client.post("/pulse")
    body = client.get("/analytics/headline").json()
    assert "signals" in body["headline"]
    assert "proposals" in body["headline"]
    assert "never generated" in body["headline"]


def test_decision_search_filters_compose(client):
    report = client.post("/pulse").json()
    cost_link = report["chain"][0]
    client.post(
        f"/actions/{cost_link['action_id']}/reject",
        json={"actor": "op", "rationale": "known migration window"},
    )
    assert client.get("/decisions").json()["count"] == 1
    assert client.get("/decisions", params={"verdict": "approved"}).json()["count"] == 0
    hits = client.get("/decisions", params={"q": "migration"}).json()
    assert hits["count"] == 1
    assert hits["decisions"][0]["rationale"] == "known migration window"
    # LIKE wildcards in the query are literals, not patterns
    assert client.get("/decisions", params={"q": "%"}).json()["count"] == 0


def test_proposed_actions_carry_the_ttl_badge(client, monkeypatch):
    client.post("/pulse")
    action = client.get("/actions").json()["actions"][0]
    assert action["expires_in_hours"] is not None
    assert 71.0 < action["expires_in_hours"] <= 72.0  # default TTL, just filed
    monkeypatch.setenv("SENTINEL_ACTION_TTL_HOURS", "0")  # disabled -> no badge
    action = client.get("/actions").json()["actions"][0]
    assert action["expires_in_hours"] is None


def test_telemetry_counts_overturned_debates(client):
    client.post("/pulse")  # fake skeptic agrees -> no overturns
    telemetry = client.get("/analytics/decisions").json()["telemetry"]
    assert telemetry["debates"] >= 1
    assert telemetry["debates_overturned"] == 0
