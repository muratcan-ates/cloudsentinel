"""Tests for the value pack (Sprint 3): self-FinOps, forecast, what-if, ROI.

Acceptance criteria: every figure is hand-reproducible arithmetic (the
forecast is asserted against an exact least-squares computation on a
synthetic series), the AI usage counters follow the ledger, and the ROI
report never invents an observation it does not have.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import analytics, db
from app.analytics import RPD_ASSUMPTION, monthly_budget
from tests.test_recommender import seed_analyzed_event
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# --- /analytics/ai ---------------------------------------------------------------


def test_ai_usage_counters_follow_the_ledger(client):
    conn = db.connect()
    try:
        db.init_db()
        db.record_ai_usage(
            conn, agent="analyst", model="m", source="fake", prompt="a", from_cache=True
        )
        db.record_ai_usage(
            conn, agent="recommender", model="m", source="gemini", prompt="b"
        )
        db.record_ai_usage(
            conn, agent="recommender", model="rule-based", source="fallback", prompt="c"
        )
    finally:
        conn.close()
    body = client.get("/analytics/ai").json()
    assert body["requests_total"] == 3
    assert body["live_calls"] == 1
    assert body["cache_hits"] == 1
    assert body["fallback_answers"] == 1
    assert body["live_calls_today"] == 1
    assert body["rpd_assumption"] == RPD_ASSUMPTION
    assert body["rpd_used_pct"] == round(1 / RPD_ASSUMPTION * 100, 1)
    assert body["by_agent"] == {"analyst": 1, "recommender": 2}
    assert body["last_seven_days"][-1]["calls"] == 3
    assert "billing-disabled" in body["note"]


def test_ai_usage_empty_ledger_is_zeroes(client):
    body = client.get("/analytics/ai").json()
    assert body["requests_total"] == 0
    assert body["rpd_used_pct"] == 0.0
    assert body["last_seven_days"] == []


# --- /analytics/costs/forecast ---------------------------------------------------


LINEAR_DATASET = {
    "currency": "USD",
    "period": {"start": "2026-07-01", "end": "2026-07-04"},
    "daily_costs": [
        {"service": "svc", "date": f"2026-07-0{i + 1}", "cost": 10.0 * (i + 1)}
        for i in range(4)
    ],
}


def test_forecast_matches_hand_least_squares(client, monkeypatch):
    """10,20,30,40 -> slope 10, intercept 10; July has 27 unobserved days:
    predictions 50..310 sum to 4860, so the projection is 100 + 4860."""
    monkeypatch.setattr(analytics, "load_dataset", lambda: LINEAR_DATASET)
    body = client.get("/analytics/costs/forecast").json()
    assert body["history_days"] == 4
    assert body["slope_per_day"] == 10.0
    assert body["month"] == "2026-07"
    assert body["days_in_month"] == 31
    assert body["observed_days_in_month"] == 4
    assert body["month_to_date"] == 100.0
    assert body["projected_month_total"] == 4960.0
    assert body["monthly_budget"] is None
    assert body["projected_over_budget"] is None


def test_forecast_budget_signal(client, monkeypatch):
    monkeypatch.setattr(analytics, "load_dataset", lambda: LINEAR_DATASET)
    monkeypatch.setenv("SENTINEL_MONTHLY_BUDGET", "4000")
    body = client.get("/analytics/costs/forecast").json()
    assert body["monthly_budget"] == 4000.0
    assert body["projected_over_budget"] is True
    monkeypatch.setenv("SENTINEL_MONTHLY_BUDGET", "10000")
    assert client.get("/analytics/costs/forecast").json()["projected_over_budget"] is False


def test_monthly_budget_parsing(monkeypatch):
    for garbage in ("", "abc", "-5", "0"):
        monkeypatch.setenv("SENTINEL_MONTHLY_BUDGET", garbage)
        assert monthly_budget() is None
    monkeypatch.setenv("SENTINEL_MONTHLY_BUDGET", "5000")
    assert monthly_budget() == 5000.0


def test_forecast_on_the_mock_dataset_is_consistent(client):
    daily = client.get("/costs/daily").json()
    body = client.get("/analytics/costs/forecast").json()
    july = round(
        sum(t for d, t in zip(daily["dates"], daily["totals"]) if d.startswith("2026-07")),
        2,
    )
    assert body["month_to_date"] == july
    assert body["projected_month_total"] >= body["month_to_date"]


# --- /analytics/whatif -----------------------------------------------------------


def test_whatif_projects_the_preferred_saving(client):
    event_id = seed_analyzed_event(service="compute", occurred_on="2026-07-01")
    recommendation = client.post(f"/anomalies/{event_id}/recommend").json()
    action_id = recommendation["action_id"]
    saving = (
        recommendation["savings"]["bold_monthly"]
        if recommendation["preferred"] == "BOLD"
        else recommendation["savings"]["cautious_monthly"]
    )
    daily = client.get("/costs/daily").json()
    compute = next(s for s in daily["services"] if s["service"] == "compute")
    current = round(sum(compute["values"]) / len(compute["values"]) * 30, 2)

    body = client.get("/analytics/whatif", params={"action_id": action_id}).json()
    assert body["service"] == "compute"
    assert body["current_monthly_projection"] == current
    assert body["monthly_saving_if_executed"] == round(saving, 2)
    assert body["with_action_monthly_projection"] == round(max(0.0, current - saving), 2)
    assert "simulated" in body["note"]


def test_whatif_unknown_action_404(client):
    assert client.get("/analytics/whatif", params={"action_id": 999}).status_code == 404


def test_whatif_without_savings_409(client):
    conn = db.connect()
    try:
        db.init_db()
        with db.writing(conn):
            cursor = conn.execute(
                "INSERT INTO actions (event_id, title, detail_json) "
                "VALUES (NULL, 'bare', '{}')"
            )
            action_id = cursor.lastrowid
    finally:
        conn.close()
    assert (
        client.get("/analytics/whatif", params={"action_id": action_id}).status_code
        == 409
    )


# --- /analytics/roi --------------------------------------------------------------


def test_roi_is_honest_without_post_decision_days(client):
    event_id = seed_analyzed_event(service="compute", occurred_on="2026-07-01")
    action_id = client.post(f"/anomalies/{event_id}/recommend").json()["action_id"]
    client.post(f"/actions/{action_id}/approve")
    body = client.get("/analytics/roi").json()
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["status"] == "estimated_only"  # mock window ends before decisions
    assert row["after_days"] == 0
    assert row["observed_monthly_delta"] is None
    assert row["estimated_monthly_saving"] > 0


def test_roi_observes_before_after_when_data_exists(client, monkeypatch):
    """A decision backdated into the series splits it 20,20 | 10,10:
    before mean 20, after mean 10, observed delta (20-10)*30 = 300."""
    dataset = {
        "currency": "USD",
        "period": {"start": "2026-07-01", "end": "2026-07-04"},
        "daily_costs": [
            {"service": "svc", "date": "2026-07-01", "cost": 20.0},
            {"service": "svc", "date": "2026-07-02", "cost": 20.0},
            {"service": "svc", "date": "2026-07-03", "cost": 10.0},
            {"service": "svc", "date": "2026-07-04", "cost": 10.0},
        ],
    }
    monkeypatch.setattr(analytics, "load_dataset", lambda: dataset)
    detail = {
        "preferred": "CAUTIOUS",
        "savings": {"cautious_monthly": 250.0, "bold_monthly": 500.0},
        "anomaly": {"service": "svc"},
    }
    conn = db.connect()
    try:
        db.init_db()
        with db.writing(conn):
            conn.execute(
                "INSERT INTO actions (event_id, title, detail_json, state, "
                "decided_at, decided_by) VALUES (NULL, 'obs', ?, 'approved', "
                "'2026-07-02 12:00:00', 'operator')",
                (json.dumps(detail),),
            )
    finally:
        conn.close()
    row = client.get("/analytics/roi").json()["rows"][0]
    assert row["status"] == "observed"
    assert row["before_daily_mean"] == 20.0
    assert row["after_daily_mean"] == 10.0
    assert row["after_days"] == 2
    assert row["observed_monthly_delta"] == 300.0
    assert row["estimated_monthly_saving"] == 250.0
