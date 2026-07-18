"""Tests for the demo-operations pack: date rebase, pulse persistence,
per-run budget override, env-gated demo reset, read-only showcase mode,
the extended health check and the JSON failure envelope.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import main as main_module
from app import db
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# --- date rebase -----------------------------------------------------------------


def test_rebase_shifts_every_lane_by_the_same_whole_weeks(client, monkeypatch):
    from app.detection import load_dataset
    from app.fraud import load_fraud_dataset
    from app.security import load_security_dataset

    frozen_end = date.fromisoformat(load_dataset()["period"]["end"])
    security_before = max(r["date"] for r in load_security_dataset()["daily_counts"])
    fraud_before = max(e["date"] for e in load_fraud_dataset()["events"])

    monkeypatch.setenv("SENTINEL_REBASE_DATES", "1")
    rebased = load_dataset()
    rebased_end = date.fromisoformat(rebased["period"]["end"])
    shift = (rebased_end - frozen_end).days
    assert shift % 7 == 0  # whole weeks: weekday alignment survives
    assert rebased_end.weekday() == frozen_end.weekday()
    yesterday = date.today() - timedelta(days=1)
    assert timedelta(0) <= yesterday - rebased_end < timedelta(days=7)

    # the other lanes move by the SAME delta — same-day correlations hold
    security_after = max(r["date"] for r in load_security_dataset()["daily_counts"])
    fraud_after = max(e["date"] for e in load_fraud_dataset()["events"])
    delta = timedelta(days=shift)
    assert date.fromisoformat(security_after) == date.fromisoformat(security_before) + delta
    assert date.fromisoformat(fraud_after) == date.fromisoformat(fraud_before) + delta


def test_rebase_off_by_default(client):
    from app.detection import demo_rebase_delta

    assert demo_rebase_delta() == timedelta(0)


# --- pulse persistence and per-run budget ----------------------------------------


def test_pulse_last_replays_the_most_recent_run(client):
    assert client.get("/pulse/last").status_code == 404
    ran = client.post("/pulse").json()
    last = client.get("/pulse/last").json()
    assert last["ran_at"]
    assert last["report"]["signals"] == ran["signals"]
    assert last["report"]["briefing"]["headline"] == ran["briefing"]["headline"]


def test_pulse_budget_query_param_overrides_for_one_run(client):
    dry = client.post("/pulse", params={"llm_budget": 0}).json()
    assert dry["llm_budget"] == 0
    assert dry["budget_exhausted"] is True
    assert dry["briefing"]["source"] == "fallback"
    # the override is per-run: the next pulse is back on the default cap
    normal = client.post("/pulse").json()
    assert normal["llm_budget"] > 0


# --- demo reset ------------------------------------------------------------------


def test_demo_reset_is_a_404_without_the_knob(client):
    assert client.post("/ops/demo-reset").status_code == 404


def test_demo_reset_clears_state_but_preserves_the_ai_ledger(client, monkeypatch):
    client.post("/pulse")
    conn = db.connect()
    try:
        usage_before = conn.execute("SELECT count(*) FROM ai_usage").fetchone()[0]
    finally:
        conn.close()
    assert usage_before > 0

    monkeypatch.setenv("SENTINEL_DEMO_RESET", "1")
    body = client.post("/ops/demo-reset", params={"seed": 1}).json()
    assert body["seeded_decisions"] == 6
    assert "preserved" in body["note"]

    conn = db.connect()
    try:
        for table in ("events", "actions", "idempotency", "pulse_log"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        decisions = conn.execute("SELECT count(*) FROM decisions").fetchone()[0]
        usage_after = conn.execute("SELECT count(*) FROM ai_usage").fetchone()[0]
    finally:
        conn.close()
    assert decisions == 6  # the seed, nothing else
    assert usage_after == usage_before  # quota history never rewritten

    # seeded verdicts feed decision memory exactly like real ones
    similar = client.get("/decisions/similar", params={"service": "compute"}).json()
    assert similar["count"] == 2
    assert any("migration window" in (d["rationale"] or "") for d in similar["decisions"])


# --- read-only showcase mode -----------------------------------------------------


def test_readonly_blocks_every_post_but_keeps_reads(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_READONLY", "1")
    assert client.post("/pulse").status_code == 403
    assert client.post("/anomalies/1/analyze").status_code == 403
    assert client.post("/actions/1/approve").status_code == 403
    body = client.post("/pulse").json()
    assert "read-only" in body["detail"]
    assert client.get("/anomalies").status_code == 200
    assert client.get("/health").json()["readonly"] is True


# --- health and failure envelope -------------------------------------------------


def test_health_reports_version_provider_and_mode(client):
    body = client.get("/health").json()
    assert body["version"] == app.version
    assert body["provider"] == "fake"  # conftest pins SENTINEL_FAKE_LLM=1
    assert body["readonly"] is False


def test_failures_answer_with_a_json_envelope(monkeypatch):
    import sqlite3

    def busy():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(main_module, "load_dataset", busy)
    with TestClient(app, raise_server_exceptions=False) as raw_client:
        response = raw_client.get("/costs/summary")
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "2"
        assert response.json() == {"detail": "database is busy — retry shortly"}

    def broken():
        raise ValueError("boom")

    monkeypatch.setattr(main_module, "load_dataset", broken)
    with TestClient(app, raise_server_exceptions=False) as raw_client:
        response = raw_client.get("/costs/summary")
        assert response.status_code == 500
        assert response.json() == {"detail": "internal server error"}
