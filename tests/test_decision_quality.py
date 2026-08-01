"""Decision-quality metrics: is the DESK working, not is the model bigger."""

import json

import pytest
from fastapi.testclient import TestClient

from app import analytics, db, history
from main import app


@pytest.fixture
def client():
    return TestClient(app)


def _decided_card(conn, service="compute", severity="critical", verdict="approved",
                  model="gemini-flash-latest", occurred_on="2026-07-01"):
    detail = {
        "model": model,
        "anomaly": {"service": service, "severity": severity, "z_score": 4.0},
        "confidence": {"score": 0.8, "rationale": "r"},
        "trace": [
            {
                "step": "analyst",
                "confidence": 0.9,
                "uncertainty_sources": [
                    {"code": "short_baseline", "label": "short baseline", "detail": "d"}
                ],
            },
            {
                "step": "recommender",
                "confidence": 0.7,
                "uncertainty_sources": [
                    {"code": "short_baseline", "label": "short baseline", "detail": "d"},
                    {
                        "code": "no_decision_memory",
                        "label": "no operator precedent",
                        "detail": "d",
                    },
                ],
            },
        ],
    }
    body = json.dumps(detail)
    with db.writing(conn):
        event = conn.execute(
            "INSERT INTO events (kind, service, occurred_on, payload_json) "
            "VALUES ('cost_anomaly', ?, ?, '{}') RETURNING id",
            (service, occurred_on),
        ).fetchone()
        action = conn.execute(
            "INSERT INTO actions (event_id, title, detail_json, state, decided_at, "
            "decided_by) VALUES (?, 't', ?, ?, datetime('now'), 'operator:murat') "
            "RETURNING id",
            (event["id"], body, verdict),
        ).fetchone()
        history.record(conn, action["id"], "filed", "agent:recommender")
        conn.execute(
            "INSERT INTO decisions "
            "(action_id, service, verdict, rationale, input_context_json) "
            "VALUES (?, ?, ?, 'because', ?)",
            (action["id"], service, verdict, body),
        )
        history.record(conn, action["id"], verdict, "operator:murat", "because")
    return action["id"]


def test_an_empty_desk_reports_nothing_rather_than_zero(client):
    body = client.get("/analytics/quality").json()
    assert body["human_decisions"] == 0
    assert body["acceptance_rate"] is None
    assert body["mean_time_to_decision_hours"] is None
    assert body["intelligence_cost"]["calls_per_decision"] is None


def test_acceptance_is_sliced_by_service_severity_and_model(client):
    conn = db.connect_ready()
    try:
        _decided_card(conn, service="compute", severity="critical", verdict="approved")
        _decided_card(
            conn, service="compute", severity="warning", verdict="rejected",
            occurred_on="2026-07-02",
        )
        _decided_card(
            conn, service="storage", severity="critical", verdict="approved",
            model="gemini-flash-lite-latest", occurred_on="2026-07-03",
        )
    finally:
        conn.close()
    body = client.get("/analytics/quality").json()
    assert body["human_decisions"] == 3
    assert body["acceptance_rate"] == round(2 / 3, 4)
    rows = {(r["dimension"], r["key"]): r for r in body["acceptance"]}
    assert rows[("service", "compute")]["decided"] == 2
    assert rows[("service", "compute")]["acceptance_rate"] == 0.5
    assert rows[("severity", "critical")]["acceptance_rate"] == 1.0
    assert rows[("severity", "warning")]["acceptance_rate"] == 0.0
    assert rows[("agent_model", "gemini-flash-lite-latest")]["decided"] == 1


def test_latency_comes_from_the_trail_not_from_the_action_row(client):
    """A reopen overwrites actions.decided_at; the trail keeps the first
    deliberation, so the figure cannot be flattered by reopening a card."""
    conn = db.connect_ready()
    try:
        action_id = _decided_card(conn)
        with db.writing(conn):
            conn.execute(
                "UPDATE action_events SET created_at = datetime(created_at, '-6 hours') "
                "WHERE transition = 'filed' AND action_id = ?",
                (action_id,),
            )
    finally:
        conn.close()
    body = client.get("/analytics/quality").json()
    assert body["mean_time_to_decision_hours"] == 6.0
    assert body["median_time_to_decision_hours"] == 6.0


def test_recurrence_counts_the_times_a_service_came_back(client):
    conn = db.connect_ready()
    try:
        for day in ("2026-07-01", "2026-07-04", "2026-07-09"):
            _decided_card(conn, service="compute", occurred_on=day)
        _decided_card(conn, service="storage", occurred_on="2026-07-02")
    finally:
        conn.close()
    rows = {r["service"]: r for r in client.get("/analytics/quality").json()["recurrence"]}
    assert rows["compute"]["anomaly_days"] == 3
    assert rows["compute"]["recurrences"] == 2  # first sighting is not a return
    assert rows["compute"]["first_seen"] == "2026-07-01"
    assert rows["compute"]["last_seen"] == "2026-07-09"
    assert rows["storage"]["recurrences"] == 0


def test_intelligence_cost_counts_quota_not_dollars_by_default(client):
    conn = db.connect_ready()
    try:
        _decided_card(conn)
        with db.writing(conn):
            for _ in range(4):
                db.record_ai_usage(
                    conn, agent="analyst", model="m", source="gemini", prompt="p"
                )
            db.record_ai_usage(
                conn, agent="analyst", model="m", source="gemini", prompt="p",
                from_cache=True,
            )
    finally:
        conn.close()
    cost = client.get("/analytics/quality").json()["intelligence_cost"]
    assert cost["llm_calls"] == 5
    assert cost["live_calls"] == 4
    assert cost["cached_calls"] == 1
    assert cost["calls_per_decision"] == 5.0
    assert cost["live_calls_per_decision"] == 4.0
    # Billing-disabled by construction: no price configured, no dollar claim.
    assert cost["price_per_call_usd"] is None
    assert cost["usd_per_decision"] is None
    assert "billing-disabled" in cost["note"]


def test_a_configured_price_turns_calls_into_dollars(client, monkeypatch):
    monkeypatch.setenv(analytics.LLM_PRICE_ENV, "0.002")
    conn = db.connect_ready()
    try:
        _decided_card(conn)
        with db.writing(conn):
            for _ in range(4):
                db.record_ai_usage(
                    conn, agent="analyst", model="m", source="gemini", prompt="p"
                )
    finally:
        conn.close()
    cost = client.get("/analytics/quality").json()["intelligence_cost"]
    assert cost["price_per_call_usd"] == 0.002
    assert cost["usd_per_decision"] == 0.008


def test_a_nonsense_price_is_ignored_rather_than_obeyed(monkeypatch):
    for raw in ("", "free", "-1", "0"):
        monkeypatch.setenv(analytics.LLM_PRICE_ENV, raw)
        assert analytics.llm_price_per_call() is None


def test_confidence_and_uncertainty_aggregate_across_every_agent(client):
    conn = db.connect_ready()
    try:
        _decided_card(conn)
        _decided_card(conn, service="storage", occurred_on="2026-07-05")
    finally:
        conn.close()
    body = client.get("/analytics/quality").json()
    # Two cards, two hops each: (0.9 + 0.7) / 2
    assert body["avg_agent_confidence"] == 0.8
    top = body["top_uncertainty_sources"]
    assert top[0]["code"] == "short_baseline"
    assert top[0]["occurrences"] == 4
    assert top[0]["label"] == "short baseline"
    assert {t["code"] for t in top} == {"short_baseline", "no_decision_memory"}


def test_calibration_rides_along_so_the_desk_is_one_read(client):
    conn = db.connect_ready()
    try:
        _decided_card(conn)
    finally:
        conn.close()
    body = client.get("/analytics/quality").json()
    assert [bucket["range"] for bucket in body["calibration"]] == [
        "0.0–0.4", "0.4–0.6", "0.6–0.8", "0.8–1.0"
    ]
    assert body["calibration"][3]["decisions"] == 1  # the 0.8 card
    assert "confidence" in body["calibration_method"]


def test_corrupt_context_degrades_to_a_skipped_slice_not_a_500(client):
    conn = db.connect_ready()
    try:
        _decided_card(conn)
        with db.writing(conn):
            conn.execute(
                "INSERT INTO decisions (action_id, service, verdict, "
                "input_context_json) VALUES (NULL, 'compute', 'approved', 'nonsense')"
            )
    finally:
        conn.close()
    body = client.get("/analytics/quality").json()
    assert body["human_decisions"] == 2
    rows = {(r["dimension"], r["key"]): r for r in body["acceptance"]}
    assert rows[("severity", "unstated")]["decided"] == 1
    assert rows[("agent_model", "unstated")]["decided"] == 1
