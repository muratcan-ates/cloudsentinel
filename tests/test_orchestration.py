"""Tests for the orchestration-depth pack: the per-chain agent trace,
decision-memory visibility and the chronicler's pulse briefing.

Everything here runs under the fake provider (conftest pins
SENTINEL_FAKE_LLM=1) — the trace, the memory counter and the briefing
must be observable without a live key, because that is how the demo runs.
"""

import pytest
from fastapi.testclient import TestClient

from app import chronicler, db
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _first_event_id(client):
    report = client.get("/anomalies").json()
    assert report["anomalies"], "the planted dataset must produce signals"
    return report["anomalies"][0]["id"], report["anomalies"][0]["service"]


# --- agent trace -----------------------------------------------------------------


def test_recommendation_carries_the_agent_trace(client):
    event_id, _ = _first_event_id(client)
    client.post(f"/anomalies/{event_id}/analyze")
    body = client.post(f"/anomalies/{event_id}/recommend").json()

    steps = [entry["step"] for entry in body["trace"]]
    assert steps[:3] == ["analyst", "memory", "recommender"]
    by_step = {entry["step"]: entry for entry in body["trace"]}
    # the analyst hop carries the measured cost of the original work
    assert by_step["analyst"]["duration_ms"] >= 0
    assert by_step["analyst"]["source"] == "fake"
    assert by_step["recommender"]["duration_ms"] >= 0
    assert by_step["recommender"]["from_cache"] is False
    # fake confidence (0.5) sits under the debate bar, so the skeptic ran
    assert "skeptic" in steps
    assert by_step["skeptic"]["revised"] in (True, False)

    # the trace is part of the persisted evidence pack, not just the response
    action = client.get("/actions").json()["actions"][0]
    assert [e["step"] for e in action["detail"]["trace"]] == steps


def test_trace_marks_the_fallback_lane_honestly(client, monkeypatch):
    from app import analyst, recommender
    from tests.test_analyst import UnavailableProvider

    monkeypatch.setattr(analyst, "get_provider", lambda: UnavailableProvider())
    monkeypatch.setattr(recommender, "get_provider", lambda: UnavailableProvider())
    event_id, _ = _first_event_id(client)
    client.post(f"/anomalies/{event_id}/analyze")
    body = client.post(f"/anomalies/{event_id}/recommend").json()

    by_step = {entry["step"]: entry for entry in body["trace"]}
    assert by_step["analyst"]["source"] == "fallback"
    assert by_step["recommender"]["source"] == "fallback"
    assert "skeptic" not in by_step  # debate never burns quota on fallbacks


# --- decision memory visibility --------------------------------------------------


def test_memory_considered_surfaces_past_verdicts(client):
    event_id, service = _first_event_id(client)
    client.post(f"/anomalies/{event_id}/analyze")
    first = client.post(f"/anomalies/{event_id}/recommend").json()
    assert first["memory_considered"] == 0  # no verdicts exist yet

    client.post(
        f"/actions/{first['action_id']}/reject",
        json={"actor": "operator", "rationale": "known migration window"},
    )
    second = client.post(f"/anomalies/{event_id}/recommend").json()
    assert second["memory_considered"] == 1
    memory = client.get("/actions").json()["actions"][-1]["detail"]["memory"]
    assert memory["count"] == 1
    assert "known migration window" in memory["entries"][0]
    trace_memory = next(
        entry
        for entry in second["trace"]
        if entry["step"] == "memory"
    )
    assert trace_memory["entries"] == 1
    assert service  # the digest is service-scoped by construction


# --- chronicler briefing ---------------------------------------------------------


def test_pulse_report_carries_the_briefing(client):
    body = client.post("/pulse").json()
    briefing = body["briefing"]
    assert briefing is not None
    assert briefing["headline"]
    assert briefing["summary"]
    assert briefing["watch_next"]
    assert briefing["source"] in {"fake", "fallback"}

    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT count(*) FROM ai_usage WHERE agent = 'chronicler'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert rows == 1  # exactly one ledgered briefing call per pulse


def test_chronicler_briefing_is_served_from_cache(client):
    # identical facts twice: the first call spends a (fake) call and caches,
    # the second replays the envelope without spending — same as the analyst
    # and recommender lanes, whose cache-hit paths are already covered.
    facts = {
        "cost_signals": 2,
        "security_signals": 1,
        "fraud_flagged": 0,
        "cross_lane_cards": 0,
        "analyzed": 2,
        "proposals_filed": 2,
        "proposals_reused": 0,
        "top_service": "network",
    }
    conn = db.connect()
    try:
        first = chronicler.write_briefing(conn, facts)
        second = chronicler.write_briefing(conn, facts)
    finally:
        conn.close()

    assert first["source"] == "fake"
    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert second["headline"] == first["headline"]
    assert second["summary"] == first["summary"]
    assert second["watch_next"] == first["watch_next"]

    conn = db.connect()
    try:
        usage = conn.execute(
            "SELECT from_cache FROM ai_usage WHERE agent = 'chronicler' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert [row["from_cache"] for row in usage] == [0, 1]


def test_dry_budget_briefing_falls_back_deterministically(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_PULSE_LLM_BUDGET", "0")
    body = client.post("/pulse").json()
    briefing = body["briefing"]
    assert briefing["source"] == "fallback"
    assert briefing["model"] == "rule-based"
    # the deterministic narrative restates the run's computed facts
    assert str(body["signals"]) in briefing["headline"]


# --- context-aware fake lane -----------------------------------------------------


def test_fake_lane_narrates_the_actual_signal(client):
    """Under SENTINEL_FAKE_LLM the cards must read like the real anomaly,
    not like schema filler — the jury-day quota insurance."""
    report = client.get("/anomalies").json()
    anomaly = report["anomalies"][0]
    analysis = client.post(f"/anomalies/{anomaly['id']}/analyze").json()
    assert analysis["source"] == "fake"
    assert anomaly["service"] in analysis["summary"]
    assert "fake" not in analysis["summary"]
    assert analysis["evidence_ids"]  # cited rows ring the evidence chart

    recommendation = client.post(f"/anomalies/{anomaly['id']}/recommend").json()
    titles = " ".join(option["title"] for option in recommendation["options"])
    assert anomaly["service"] in titles
    if recommendation["transcript"] is not None:
        assert "stance" in recommendation["transcript"]["skeptic_rationale"]

    pulse = client.post("/pulse").json()
    assert pulse["briefing"]["source"] == "fake"
    assert "signal" in pulse["briefing"]["headline"]
    assert "fake" not in pulse["briefing"]["headline"]


def test_unregistered_schemas_keep_the_generic_fake(client):
    from pydantic import BaseModel

    from app.llm import FakeProvider

    class UnknownShape(BaseModel):
        note: str

    result = FakeProvider().generate("anything", response_schema=UnknownShape)
    assert result.parsed.note == "fake"
