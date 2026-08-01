"""Run receipts: what one watch cycle actually cost, itemised."""

import json

import pytest
from fastapi.testclient import TestClient

from app import analytics, db, telemetry
from main import app


@pytest.fixture
def client():
    return TestClient(app)


def _log_pulse(conn, chain, *, reflex_ms=0.5, calls=4, budget=14, exhausted=False):
    report = {
        "threshold": 3.0,
        "mission": "finops",
        "reflex_ms": reflex_ms,
        "signals": len(chain),
        "analyzed": len(chain),
        "proposals_filed": sum(1 for link in chain if not link.get("reused")),
        "proposals_reused": sum(1 for link in chain if link.get("reused")),
        "llm_budget": budget,
        "llm_calls_used": calls,
        "budget_exhausted": exhausted,
        "chain": chain,
    }
    with db.writing(conn):
        conn.execute(
            "INSERT INTO pulse_log (report_json) VALUES (?)", (json.dumps(report),)
        )


def _card(conn, hops):
    with db.writing(conn):
        row = conn.execute(
            "INSERT INTO actions (event_id, title, detail_json) "
            "VALUES (NULL, 't', ?) RETURNING id",
            (json.dumps({"trace": hops}),),
        ).fetchone()
    return row["id"]


def test_no_pulse_yet_reports_an_empty_bill(client):
    body = client.get("/analytics/receipts").json()
    assert body["count"] == 0
    assert body["receipts"] == []
    assert body["totals"]["agent_turns"] == 0


def test_a_receipt_itemises_turns_and_measured_time(client):
    conn = db.connect_ready()
    try:
        action_id = _card(
            conn,
            [
                {"step": "analyst", "duration_ms": 120.0},
                {"step": "memory", "entries": 3},
                {"step": "recommender", "duration_ms": 200.0},
                {"step": "panel", "answered": 3, "duration_ms": 300.0},
            ],
        )
        _log_pulse(conn, [{"action_id": action_id, "reused": False}], reflex_ms=1.5)
    finally:
        conn.close()
    receipt = client.get("/analytics/receipts").json()["receipts"][0]
    # 'memory' is a SQL read, not an agent turn.
    assert receipt["agent_turns"] == 3
    assert receipt["panel_seats_answered"] == 3
    assert receipt["agent_ms"] == 620.0
    assert receipt["wall_clock_ms"] == 621.5
    assert receipt["reflex_ms"] == 1.5
    assert receipt["unmeasured_turns"] == 0


def test_a_reused_card_is_not_charged_to_the_run_that_only_recognised_it(client):
    """An idempotent re-poll must not read as expensive as the first sweep."""
    conn = db.connect_ready()
    try:
        action_id = _card(
            conn,
            [
                {"step": "analyst", "duration_ms": 120.0},
                {"step": "recommender", "duration_ms": 200.0},
            ],
        )
        _log_pulse(conn, [{"action_id": action_id, "reused": False}], calls=9)
        _log_pulse(conn, [{"action_id": action_id, "reused": True}], calls=1)
    finally:
        conn.close()
    receipts = client.get("/analytics/receipts").json()["receipts"]
    assert receipts[0]["agent_turns"] == 0  # newest: reused only
    assert receipts[0]["agent_ms"] == 0.0
    assert receipts[0]["proposals_reused"] == 1
    assert receipts[1]["agent_turns"] == 2  # the run that earned the card


def test_hops_persisted_before_durations_existed_are_counted_and_declared(client):
    """Turns without a measurement are named, never guessed at."""
    conn = db.connect_ready()
    try:
        action_id = _card(
            conn, [{"step": "analyst"}, {"step": "recommender", "duration_ms": 50.0}]
        )
        _log_pulse(conn, [{"action_id": action_id, "reused": False}], reflex_ms=None)
    finally:
        conn.close()
    receipt = client.get("/analytics/receipts").json()["receipts"][0]
    assert receipt["agent_turns"] == 2
    assert receipt["unmeasured_turns"] == 1
    assert receipt["agent_ms"] == 50.0
    assert receipt["reflex_ms"] is None
    assert receipt["wall_clock_ms"] == 50.0


def test_the_budget_verdict_rides_the_receipt(client):
    conn = db.connect_ready()
    try:
        _log_pulse(conn, [], calls=14, budget=14, exhausted=True)
    finally:
        conn.close()
    receipt = client.get("/analytics/receipts").json()["receipts"][0]
    assert receipt["llm_calls_used"] == 14
    assert receipt["llm_budget"] == 14
    assert receipt["budget_exhausted"] is True


def test_money_appears_only_when_a_call_is_priced(client, monkeypatch):
    conn = db.connect_ready()
    try:
        _log_pulse(conn, [], calls=10)
    finally:
        conn.close()
    unpriced = client.get("/analytics/receipts").json()
    assert unpriced["receipts"][0]["usd"] is None
    assert unpriced["totals"]["usd"] is None

    monkeypatch.setenv(analytics.LLM_PRICE_ENV, "0.0005")
    priced = client.get("/analytics/receipts").json()
    assert priced["receipts"][0]["usd"] == 0.005
    assert priced["totals"]["usd"] == 0.005


def test_receipts_are_newest_first_and_limited(client):
    conn = db.connect_ready()
    try:
        for _ in range(5):
            _log_pulse(conn, [])
    finally:
        conn.close()
    body = client.get("/analytics/receipts?limit=2").json()
    assert body["count"] == 2
    assert [r["pulse_id"] for r in body["receipts"]] == [5, 4]


def test_a_corrupt_pulse_row_is_skipped_not_fatal(client):
    conn = db.connect_ready()
    try:
        _log_pulse(conn, [])
        with db.writing(conn):
            conn.execute("INSERT INTO pulse_log (report_json) VALUES ('not json')")
    finally:
        conn.close()
    body = client.get("/analytics/receipts").json()
    assert body["count"] == 1


def test_a_real_pulse_produces_a_real_receipt(client):
    """End to end against the actual chain, not a fixture."""
    report = client.post("/pulse").json()
    receipt = client.get("/analytics/receipts").json()["receipts"][0]
    assert receipt["signals"] == report["signals"]
    assert receipt["proposals_filed"] == report["proposals_filed"]
    assert receipt["llm_calls_used"] == report["llm_calls_used"]
    assert receipt["agent_turns"] > 0
    assert receipt["agent_ms"] > 0
    assert receipt["mission"] == report["mission"]


def test_asking_for_the_receipt_costs_no_model_call(client):
    client.post("/pulse")
    conn = db.connect_ready()
    try:
        before = conn.execute("SELECT count(*) FROM ai_usage").fetchone()[0]
        client.get("/analytics/receipts")
        after = conn.execute("SELECT count(*) FROM ai_usage").fetchone()[0]
    finally:
        conn.close()
    assert before == after


def test_the_assembly_helper_is_readable_without_the_endpoint():
    """telemetry.run_receipts is the measurement; the endpoint only prices it."""
    conn = db.connect_ready()
    try:
        _log_pulse(conn, [], calls=3)
        receipts = telemetry.run_receipts(conn)
    finally:
        conn.close()
    assert len(receipts) == 1
    assert "usd" not in receipts[0]  # money is the endpoint's concern
    assert receipts[0]["llm_calls_used"] == 3
