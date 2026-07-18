"""Tests for the unified security detection lane (Sprint 3, S3-③).

Acceptance criteria: the security feed rides the SAME detection line as
the cost lane (rolling baseline, detector registry, reflex measurement),
persists its own event kind with stable ids, and never reaches the
cost-scoped LLM agents.
"""

import pytest
from fastapi.testclient import TestClient

from app import db
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_planted_security_spikes_are_flagged_critical(client):
    body = client.get("/security/signals").json()
    assert body["mission"] == "security"
    assert body["metric"] == "failed_login_count"
    assert body["reflex_ms"] is not None and body["reflex_ms"] > 0
    assert body["window_days"] == 28
    assert body["insufficient_data_services"] == []
    flagged = {(s["service"], s["date"]) for s in body["signals"]}
    assert flagged == {
        ("auth-gateway", "2026-06-29"),
        ("admin-portal", "2026-07-02"),
    }
    assert all(s["severity"] == "critical" for s in body["signals"])
    assert all(s["detector"] == "zscore" for s in body["signals"])


def test_quiet_source_stays_quiet(client):
    body = client.get("/security/signals").json()
    assert all(s["service"] != "api-edge" for s in body["signals"])


def test_security_threshold_override_governs(client):
    body = client.get("/security/signals", params={"threshold": 5}).json()
    assert body["threshold"] == 5.0
    assert body["signal_count"] == 0


def test_security_signals_persist_with_stable_ids(client):
    first = client.get("/security/signals").json()
    second = client.get("/security/signals").json()
    ids_first = sorted(s["id"] for s in first["signals"])
    ids_second = sorted(s["id"] for s in second["signals"])
    assert ids_first == ids_second  # rescans keep the same natural-key ids
    conn = db.connect()
    try:
        kinds = {
            row["kind"]
            for row in conn.execute(
                "SELECT kind FROM events WHERE id IN (?, ?)", ids_first
            )
        }
    finally:
        conn.close()
    assert kinds == {"security_anomaly"}


def test_security_events_never_reach_the_cost_agents(client):
    """The Analyst and Recommender are cost-scoped; a security event id
    must bounce with a 409, not start an LLM conversation."""
    signal = client.get("/security/signals").json()["signals"][0]
    assert client.post(f"/anomalies/{signal['id']}/analyze").status_code == 409
    assert client.post(f"/anomalies/{signal['id']}/recommend").status_code == 409


def test_security_sweep_does_not_inflate_the_hitl_funnel(client):
    """Section VI narrates the COST pipeline: security events persist in the
    same table but must not count as cost signals (they never reach the
    agents, so counting them would fake the conversion story)."""
    client.post("/pulse")  # persists 2 cost signals AND 2 security signals
    funnel = client.get("/analytics/decisions").json()["funnel"]
    assert funnel["signals"] == 2
    assert funnel["analyzed"] == 2


def test_pulse_sweeps_the_security_lane(client):
    body = client.post("/pulse").json()
    assert body["security_signals"] == 2
    conn = db.connect()
    try:
        count = conn.execute(
            "SELECT count(*) FROM events WHERE kind = 'security_anomaly'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 2
