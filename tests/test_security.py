"""Tests for the unified security detection lane (Sprint 3, S3-③).

Acceptance criteria: the security feed rides the SAME detection line as
the cost lane (rolling baseline, detector registry, reflex measurement),
persists its own event kind with stable ids, and never reaches the
cost-scoped LLM agents.
"""

import json

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
    # 14, not 28: security patterns turn over faster than spend does
    assert body["window_days"] == 14
    assert body["insufficient_data_services"] == []
    flagged = {(s["service"], s["date"]) for s in body["signals"]}
    assert flagged == {
        ("auth-gateway", "2026-06-29"),
        ("admin-portal", "2026-07-02"),
    }
    assert all(s["severity"] == "critical" for s in body["signals"])
    # MAD, not z-score: a credential burst inflates the mean it would
    # otherwise be measured against, and the median it cannot move
    assert all(s["detector"] == "mad" for s in body["signals"])


def test_quiet_source_stays_quiet(client):
    body = client.get("/security/signals").json()
    assert all(s["service"] != "api-edge" for s in body["signals"])


def test_security_threshold_override_governs(client):
    body = client.get("/security/signals", params={"threshold": 5}).json()
    assert body["threshold"] == 5.0
    # Under MAD the planted burst scores ~148 sigma — it does not inflate
    # the median the way it inflates a mean — so a bar of 5 cannot silence
    # it. The override still governs; it has to be raised past the signal.
    assert body["signal_count"] == 2


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


def test_every_signal_carries_its_attack_technique(client):
    """The lane speaks ATT&CK: a table lookup, identical on every scan."""
    signals = client.get("/security/signals").json()["signals"]
    assert signals
    by_service = {signal["service"]: signal["framework"] for signal in signals}
    assert by_service["auth-gateway"]["id"] == "T1110"
    assert by_service["admin-portal"]["id"] == "T1078.004"
    for tag in by_service.values():
        assert tag["framework"] == "MITRE ATT&CK"
        assert tag["url"].startswith("https://attack.mitre.org/techniques/")


def test_the_technique_survives_persistence(client):
    """The tag rides the persisted payload, not just the response."""
    client.get("/security/signals")
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT payload_json FROM events WHERE kind = 'security_anomaly' "
            "AND service = 'admin-portal' LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(row["payload_json"])["framework"]["id"] == "T1078.004"
