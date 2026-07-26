"""LLM-behavior contracts the sibling suites do not pin.

test_contracts.py owns the per-lane recommendation bounds on a single
card, test_actions.py the HITL state machine, test_db.py the cache-key
component SENSITIVITY, test_guardrails.py the env parsing and category
model validation. This file owns the four remaining contracts:

Acceptance criteria:
- the LLM cache key is DETERMINISTIC — same inputs give the same key,
  pinned to a SHA-256 vector so a silent algorithm change cannot slip by;
- a read-only demo GET sweep creates zero action rows — browsing the
  showcase can never file work;
- a proposal filed through the API carries a whitelisted category as
  SERVED (model validation is pinned elsewhere; this is the wire check);
- the pulse report's budget arithmetic never lies — calls used stay
  within the budget, and a zero-budget run lands honestly on the
  rule-based fallback lane.
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


# --- cache key: deterministic, pinned ---------------------------------------


def test_cache_key_is_deterministic_and_pinned():
    # same inputs, same key — twice; the pinned hex vector catches any
    # silent change to the hash recipe (algorithm, separator or order),
    # which would orphan every cached answer without failing a test
    first = db.cache_key("fake", "narrate the spike", "system voice")
    second = db.cache_key("fake", "narrate the spike", "system voice")
    assert first == second
    assert first == "b65c7bcb9013d9f601efde20f58ebe35f3a41a7030d5dacb107b2753da8cfb91"


# --- read-only demo: browsing files nothing ---------------------------------


def test_readonly_get_sweep_creates_no_action_rows(monkeypatch):
    monkeypatch.setenv("SENTINEL_READONLY", "1")
    with TestClient(app) as client:
        conn = db.connect()
        try:
            before = conn.execute("SELECT COUNT(*) AS n FROM actions").fetchone()["n"]
            for path in (
                "/actions",
                "/anomalies",
                "/insights",
                "/reflex/suggestions",
                "/routines/suggestions",
                "/metrics/backtest",
            ):
                assert client.get(path).status_code == 200
            after = conn.execute("SELECT COUNT(*) AS n FROM actions").fetchone()["n"]
        finally:
            conn.close()
        assert after == before  # the showcase can be read, never grown


# --- category whitelist, as served ------------------------------------------


def _seed_analyzed_event() -> int:
    """A persisted, REAL-triaged cost anomaly ready for /recommend."""
    payload = {
        "service": "compute",
        "date": "2026-07-12",
        "cost": 512.0,
        "service_mean": 128.0,
        "z_score": 3.5,
        "severity": "critical",
    }
    envelope = {
        "report": {
            "triage": "REAL",
            "summary": "spend rose sharply",
            "probable_cause": "unverified capacity change",
            "evidence_ids": ["E9"],
            "confidence": {"score": 0.8, "rationale": "clean history"},
        },
        "source": "fake",
        "model": "fake",
        "reflected": False,
    }
    conn = db.connect()
    try:
        with db.writing(conn):
            event_id = db.upsert_event(
                conn,
                kind="cost_anomaly",
                service="compute",
                occurred_on="2026-07-12",
                payload_json=json.dumps(payload),
            )
            conn.execute(
                "UPDATE events SET analysis_json = ? WHERE id = ?",
                (json.dumps(envelope), event_id),
            )
        return event_id
    finally:
        conn.close()


def test_filed_proposal_serves_a_whitelisted_category(client):
    whitelist = {"RIGHTSIZING", "CONFIG_REVIEW", "LIFECYCLE", "INVESTIGATION"}
    event_id = _seed_analyzed_event()
    recommendation = client.post(f"/anomalies/{event_id}/recommend")
    assert recommendation.status_code == 200
    body = recommendation.json()
    # as served on the wire...
    assert body["category"] in whitelist
    # ...and as persisted on the stored card the inbox reads from
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT detail_json FROM actions WHERE id = ?", (body["action_id"],)
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(row["detail_json"])["category"] in whitelist


# --- pulse budget arithmetic ------------------------------------------------


def test_zero_budget_pulse_reports_honest_exhaustion(client):
    body = client.post("/pulse?llm_budget=0").json()
    assert body["llm_budget"] == 0
    assert body["llm_calls_used"] <= body["llm_budget"]
    assert body["budget_exhausted"] is True
    # the chain still completed — every agent answered from the rule-based
    # fallback lane instead of failing the run
    assert body["signals"] >= 1
    assert body["briefing"]["source"] == "fallback"
