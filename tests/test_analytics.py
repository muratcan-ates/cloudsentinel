"""Tests for the LLM-free analytics layer (Sprint 3, BI package).

Acceptance criteria: the funnel, quality and telemetry figures must be
derivable by hand from the rows the pipeline persisted (no generation
anywhere), corrupt rows must degrade to "skipped" instead of a 500, and
the trend endpoint's windows must add up against /costs/daily.
"""

import pytest
from fastapi.testclient import TestClient

from app import db
from tests.test_actions import seed_stale_proposal
from tests.test_recommender import seed_analyzed_event
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def run_chain(client, *, service: str, occurred_on: str, verdict: str | None):
    """Seed an analyzed event, file a recommendation, optionally decide it."""
    event_id = seed_analyzed_event(service=service, occurred_on=occurred_on)
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    if verdict is not None:
        response = client.post(f"/actions/{body['action_id']}/{verdict}")
        assert response.status_code == 200
    return body


def preferred_saving(recommendation: dict) -> float:
    savings = recommendation["savings"]
    return (
        savings["bold_monthly"]
        if recommendation["preferred"] == "BOLD"
        else savings["cautious_monthly"]
    )


# --- /analytics/decisions -------------------------------------------------------


def test_empty_database_reports_zeros_not_errors(client):
    body = client.get("/analytics/decisions").json()
    assert body["funnel"] == {
        "signals": 0,
        "analyzed": 0,
        "proposals": 0,
        "pending": 0,
        "approved": 0,
        "rejected": 0,
        "executed": 0,
        "timeout_rejections": 0,
    }
    assert body["quality"]["human_decisions"] == 0
    assert body["quality"]["approval_rate"] is None
    assert body["quality"]["avg_decision_hours"] is None
    assert body["quality"]["approved_estimated_monthly_savings"] == 0.0
    assert body["telemetry"]["triage_distribution"] == {}
    assert body["telemetry"]["avg_confidence"] is None


def test_funnel_counts_follow_the_lifecycle(client):
    approved = run_chain(
        client, service="compute", occurred_on="2026-07-01", verdict="approve"
    )
    client.post(f"/actions/{approved['action_id']}/execute")
    run_chain(client, service="storage", occurred_on="2026-07-02", verdict="reject")
    run_chain(client, service="network", occurred_on="2026-07-03", verdict=None)

    funnel = client.get("/analytics/decisions").json()["funnel"]
    assert funnel["signals"] == 3
    assert funnel["analyzed"] == 3
    assert funnel["proposals"] == 3
    assert funnel["pending"] == 1
    assert funnel["approved"] == 0  # the approved action moved on to executed
    assert funnel["executed"] == 1
    assert funnel["rejected"] == 1
    assert funnel["timeout_rejections"] == 0


def test_quality_sums_only_approved_savings(client):
    approved = run_chain(
        client, service="compute", occurred_on="2026-07-01", verdict="approve"
    )
    run_chain(client, service="storage", occurred_on="2026-07-02", verdict="reject")
    # A pending proposal must not leak into the money metric.
    run_chain(client, service="network", occurred_on="2026-07-03", verdict=None)

    quality = client.get("/analytics/decisions").json()["quality"]
    assert quality["human_decisions"] == 2
    assert quality["approval_rate"] == 0.5
    assert quality["avg_decision_hours"] is not None
    assert quality["approved_estimated_monthly_savings"] == round(
        preferred_saving(approved), 2
    )


def test_approval_rate_is_the_approved_share_not_its_inverse(client):
    """Asymmetric split: 2 approvals + 1 rejection must read 0.6667 — a
    numerator swap (the rejection rate) would report 0.3333 instead."""
    run_chain(client, service="compute", occurred_on="2026-07-01", verdict="approve")
    run_chain(client, service="compute", occurred_on="2026-07-02", verdict="approve")
    run_chain(client, service="storage", occurred_on="2026-07-03", verdict="reject")
    quality = client.get("/analytics/decisions").json()["quality"]
    assert quality["human_decisions"] == 3
    assert quality["approval_rate"] == round(2 / 3, 4)


def test_executed_actions_keep_counting_as_approved_value(client):
    approved = run_chain(
        client, service="compute", occurred_on="2026-07-01", verdict="approve"
    )
    client.post(f"/actions/{approved['action_id']}/execute")
    quality = client.get("/analytics/decisions").json()["quality"]
    assert quality["approved_estimated_monthly_savings"] == round(
        preferred_saving(approved), 2
    )


def test_timeout_expiry_is_separated_from_human_decisions(client):
    seed_stale_proposal(hours_old=100)  # default TTL is 72h
    body = client.get("/analytics/decisions").json()  # the report itself sweeps
    assert body["funnel"]["pending"] == 0
    assert body["funnel"]["rejected"] == 1
    assert body["funnel"]["timeout_rejections"] == 1
    assert body["quality"]["human_decisions"] == 0
    assert body["quality"]["approval_rate"] is None
    assert body["quality"]["avg_decision_hours"] is None


def test_corrupt_action_detail_is_skipped_per_row_never_500(client):
    """The corrupt row is skipped while a healthy sibling still counts."""
    approved = run_chain(
        client, service="compute", occurred_on="2026-07-01", verdict="approve"
    )
    conn = db.connect()
    try:
        with db.writing(conn):
            conn.execute(
                "INSERT INTO actions (event_id, title, detail_json, state, "
                "decided_at, decided_by) VALUES (NULL, 'corrupt', "
                "'{\"savings\": \"not-a-block\"}', 'approved', "
                "datetime('now'), 'operator')"
            )
    finally:
        conn.close()
    body = client.get("/analytics/decisions")
    assert body.status_code == 200
    assert body.json()["quality"]["approved_estimated_monthly_savings"] == round(
        preferred_saving(approved), 2
    )


def seed_analysis_envelope(*, service: str, occurred_on: str, analysis_json: str) -> int:
    """An event whose analysis_json is written verbatim — corruption territory."""
    conn = db.connect()
    try:
        with db.writing(conn):
            event_id = db.upsert_event(
                conn,
                kind="cost_anomaly",
                service=service,
                occurred_on=occurred_on,
                payload_json="{}",
            )
            conn.execute(
                "UPDATE events SET analysis_json = ? WHERE id = ?",
                (analysis_json, event_id),
            )
            return event_id
    finally:
        conn.close()


def test_telemetry_averages_confidence_and_counts_triage(client):
    seed_analyzed_event(service="compute", occurred_on="2026-07-01")  # score 0.8
    seed_analysis_envelope(
        service="storage",
        occurred_on="2026-07-02",
        analysis_json=(
            '{"report": {"triage": "SEASONAL", '
            '"confidence": {"score": 0.6, "rationale": "r"}}}'
        ),
    )
    telemetry = client.get("/analytics/decisions").json()["telemetry"]
    assert telemetry["triage_distribution"] == {"REAL": 1, "SEASONAL": 1}
    # 0.7 distinguishes the mean from max (0.8), min (0.6) and first-seen.
    assert telemetry["avg_confidence"] == 0.7


def test_corrupt_analysis_rows_are_skipped_whole(client):
    seed_analyzed_event(service="compute", occurred_on="2026-07-01")  # healthy
    seed_analysis_envelope(
        service="storage", occurred_on="2026-07-02", analysis_json="not-json"
    )
    # Valid triage but corrupt confidence: must not half-count the triage.
    seed_analysis_envelope(
        service="network",
        occurred_on="2026-07-03",
        analysis_json='{"report": {"triage": "SEASONAL", "confidence": null}}',
    )
    body = client.get("/analytics/decisions")
    assert body.status_code == 200
    telemetry = body.json()["telemetry"]
    assert telemetry["triage_distribution"] == {"REAL": 1}
    assert telemetry["avg_confidence"] == 0.8


def test_telemetry_ledger_counts_are_pinned(client):
    """The ledger aggregates are asserted against hand-inserted rows, not
    against the endpoint's own output (no tautologies)."""
    conn = db.connect()
    try:
        db.record_ai_usage(
            conn, agent="analyst", model="m", source="fake", prompt="a", from_cache=True
        )
        db.record_ai_usage(
            conn, agent="recommender", model="m", source="gemini", prompt="b"
        )
        db.record_ai_usage(
            conn, agent="skeptic", model="m", source="gemini", prompt="c"
        )
    finally:
        conn.close()

    telemetry = client.get("/analytics/decisions").json()["telemetry"]
    assert telemetry["by_agent"] == {"analyst": 1, "recommender": 1, "skeptic": 1}
    assert telemetry["by_source"] == {"fake": 1, "gemini": 2}
    assert telemetry["requests_total"] == 3
    assert telemetry["cache_hits"] == 1
    assert telemetry["debates"] == 1


# --- /analytics/costs/trend -----------------------------------------------------


def test_trend_windows_partition_the_series(client):
    daily = client.get("/costs/daily").json()
    body = client.get("/analytics/costs/trend").json()
    assert body["window_days"] == 7
    assert body["dates"] == daily["dates"]
    assert body["totals"] == daily["totals"]
    # The mock dataset spans 14 days, so two 7-day windows cover it exactly.
    assert round(
        body["current_window_total"] + body["previous_window_total"], 2
    ) == round(sum(daily["totals"]), 2)
    assert body["change"] == round(
        body["current_window_total"] - body["previous_window_total"], 2
    )


def test_trend_rows_are_top_movers_first_with_consistent_direction(client):
    body = client.get("/analytics/costs/trend").json()
    changes = [abs(row["change"]) for row in body["services"]]
    assert changes == sorted(changes, reverse=True)
    for row in body["services"]:
        expected = (
            "up"
            if row["current_window_total"] > row["previous_window_total"]
            else "down"
            if row["current_window_total"] < row["previous_window_total"]
            else "flat"
        )
        assert row["direction"] == expected
        if row["previous_window_total"] == 0:
            assert row["change_pct"] is None


def test_trend_change_pct_matches_hand_arithmetic(client):
    body = client.get("/analytics/costs/trend").json()
    if body["previous_window_total"] == 0:
        assert body["change_pct"] is None
    else:
        expected = round(
            (body["current_window_total"] - body["previous_window_total"])
            / body["previous_window_total"]
            * 100,
            1,
        )
        assert body["change_pct"] == expected


def test_trend_window_wider_than_history_has_no_previous(client):
    body = client.get("/analytics/costs/trend", params={"window_days": 30}).json()
    assert body["previous_window_total"] == 0.0
    assert body["previous_window_days"] == 0
    assert body["change"] is None
    assert body["change_pct"] is None
    assert body["current_window_total"] == round(sum(body["totals"]), 2)
    assert all(row["direction"] == "insufficient_history" for row in body["services"])


def test_trend_refuses_to_compare_unequal_windows(client):
    """The mock dataset spans 14 days: window_days=10 leaves only a 4-day
    previous window. Comparing 10 days against 4 would fabricate a ~270%
    spend spike on flat data — the report must publish totals and day
    counts but no change figures."""
    body = client.get("/analytics/costs/trend", params={"window_days": 10}).json()
    assert body["current_window_days"] == 10
    assert body["previous_window_days"] == 4
    assert body["change"] is None
    assert body["change_pct"] is None
    for row in body["services"]:
        assert row["change"] is None
        assert row["change_pct"] is None
        assert row["direction"] == "insufficient_history"


def test_trend_single_day_window_matches_daily_totals(client):
    daily = client.get("/costs/daily").json()
    body = client.get("/analytics/costs/trend", params={"window_days": 1}).json()
    assert body["current_window_days"] == 1
    assert body["previous_window_days"] == 1
    assert body["current_window_total"] == daily["totals"][-1]
    assert body["previous_window_total"] == daily["totals"][-2]
    assert body["change"] == round(daily["totals"][-1] - daily["totals"][-2], 2)


def test_trend_window_days_is_bounded(client):
    assert client.get("/analytics/costs/trend", params={"window_days": 0}).status_code == 422
    assert client.get("/analytics/costs/trend", params={"window_days": 31}).status_code == 422
