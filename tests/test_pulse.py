"""Tests for the Pulse end-to-end chain (WP-7).

Acceptance criterion from the sprint plan: one mock spike flows through
detect → Analyst → [debate-lite] → Recommender → inbox card, with a
readable tagged JSON log stream.
"""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app import db
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_pulse_runs_the_full_chain_to_the_inbox(client):
    report = client.post("/pulse").json()
    assert report["signals"] >= 2  # both planted anomalies
    assert report["analyzed"] == report["signals"]
    assert report["proposals_filed"] == report["signals"]
    assert report["proposals_reused"] == 0

    for link in report["chain"]:
        assert link["triage"] in {"REAL", "SEASONAL", "DATA_ERROR", "KNOWN_CHANGE"}
        assert link["action_state"] == "proposed"
        assert link["preferred"] in {"CAUTIOUS", "BOLD"}

    inbox = client.get("/actions").json()
    assert inbox["count"] == report["signals"]
    assert {a["state"] for a in inbox["actions"]} == {"proposed"}
    # the inbox card carries the full evidence pack
    detail = inbox["actions"][0]["detail"]
    assert {"anomaly", "analysis", "options", "savings"} <= set(detail)


def test_pulse_is_idempotent_and_quota_cheap(client):
    first = client.post("/pulse").json()
    conn = db.connect()
    try:
        usage_after_first = conn.execute("SELECT count(*) FROM ai_usage").fetchone()[0]
    finally:
        conn.close()

    second = client.post("/pulse").json()
    assert second["signals"] == first["signals"]
    assert second["analyzed"] == 0  # stored analyses were reused
    assert second["proposals_filed"] == 0
    assert second["proposals_reused"] == second["signals"]

    conn = db.connect()
    try:
        usage_after_second = conn.execute("SELECT count(*) FROM ai_usage").fetchone()[0]
        actions = conn.execute("SELECT count(*) FROM actions").fetchone()[0]
    finally:
        conn.close()
    # the agents themselves spend nothing on a re-run; the chronicler's
    # briefing is the one deliberate per-pulse call (and the only new row)
    assert usage_after_second == usage_after_first + 1
    assert actions == first["signals"]  # no duplicate cards


def test_pulse_respects_the_threshold_parameter(client):
    quiet = client.post("/pulse", params={"threshold": 3.7}).json()
    assert quiet["signals"] == 0  # kills a hardcoded-threshold mutant
    loose = client.post("/pulse", params={"threshold": 2.0}).json()
    expected = client.get("/anomalies", params={"threshold": 2.0}).json()["anomaly_count"]
    assert loose["signals"] == expected
    assert expected >= 2
    assert client.post("/pulse", params={"threshold": 0}).status_code == 422


def test_pulse_emits_the_tagged_log_stream(client, caplog):
    with caplog.at_level(logging.INFO, logger="cloudsentinel"):
        report = client.post("/pulse").json()
    tags = [record.message.split(" ", 1)[0] for record in caplog.records]
    for tag in (
        "[REFLEX]",
        "[SIGNAL]",
        "[ANALYST]",
        "[DEBATE]",
        "[RECOMMENDER]",
        "[BRIEFING]",
    ):
        assert tag in tags, f"{tag} missing from the log stream"
    # one [SIGNAL] and one [ANALYST] line per hop — not just "at least one"
    assert tags.count("[REFLEX]") == 1  # the reflex pass opens the chain once
    # cost signals plus the unified security and fraud sweeps share the tag
    assert (
        tags.count("[SIGNAL]")
        == report["signals"] + report["security_signals"] + report["fraud_signals"]
    )
    assert tags.count("[ANALYST]") == report["analyzed"]
    # the payload after each tag is machine-readable JSON
    for record in caplog.records:
        tag, _, payload = record.message.partition(" ")
        if tag.startswith("[") and payload:
            json.loads(payload)


def test_agent_log_stream_reaches_real_server_output():
    """The stream must exist OUTSIDE pytest: the cloudsentinel logger owns
    an INFO-level stdout handler after the app module loads (caplog masks
    this, so pin the production wiring directly)."""
    import main  # noqa: F401 — importing wires the handler

    stream_logger = logging.getLogger("cloudsentinel")
    assert stream_logger.level == logging.INFO
    assert any(
        isinstance(handler, logging.StreamHandler) for handler in stream_logger.handlers
    )


def test_pulse_completes_on_provider_failure(client, monkeypatch):
    """A dead LLM mid-chain must not abort the pulse: every signal still
    reaches the inbox through the rule-based fallbacks."""
    from app import analyst, recommender
    from tests.test_analyst import UnavailableProvider

    monkeypatch.setattr(analyst, "get_provider", lambda: UnavailableProvider())
    monkeypatch.setattr(recommender, "get_provider", lambda: UnavailableProvider())
    report = client.post("/pulse").json()
    assert report["signals"] >= 2
    assert report["proposals_filed"] == report["signals"]
    inbox = client.get("/actions").json()["actions"]
    assert all(a["detail"]["source"] == "fallback" for a in inbox)


def test_hitl_decision_joins_the_log_stream(client, caplog):
    report = client.post("/pulse").json()
    action_id = report["chain"][0]["action_id"]
    with caplog.at_level(logging.INFO, logger="cloudsentinel"):
        client.post(f"/actions/{action_id}/approve")
        client.post(f"/actions/{action_id}/execute")
    hitl_lines = [r.message for r in caplog.records if r.message.startswith("[HITL]")]
    assert len(hitl_lines) == 2
    decide_payload = json.loads(hitl_lines[0].split(" ", 1)[1])
    assert decide_payload["transition"] == "approved"
    # ids/enums only — the operator identity must never ride the log stream
    assert set(decide_payload) == {"action_id", "transition"}
    assert json.loads(hitl_lines[1].split(" ", 1)[1])["mode"] == "SIMULATION"
