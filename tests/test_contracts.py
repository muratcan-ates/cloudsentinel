"""Cross-path output contracts for the recommendation chain.

The recommender is exercised deeply in test_recommender.py; this suite
pins the SAFETY BOUNDS that must hold no matter which lane answers — the
model, the fake+debate path, or the rule-based fallback: confidence
stays a real probability, every option states a rollback, and money is
never negative. test_analyst.py already pins the [0,1] bound on the
analysis path; this does the same for the recommendation path, where a
regression could otherwise ship an out-of-range score or a negative
saving unnoticed (the pydantic models bound neither).
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import db, recommender
from app.llm import FakeProvider, LLMUnavailableError
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


class _UnavailableProvider(FakeProvider):
    """Forces the deterministic rule-based fallback lane."""

    def generate(self, prompt, **kwargs):
        raise LLMUnavailableError("quota exhausted")

    @property
    def model(self):
        return "gemini-2.5-flash"


def _seed_analyzed_event(
    *,
    service: str = "compute",
    occurred_on: str = "2026-07-11",
    cost: float = 512.0,
    service_mean: float = 128.0,
    z_score: float = 3.5,
) -> int:
    """A persisted, REAL-triaged cost anomaly ready for /recommend."""
    payload = {
        "service": service,
        "date": occurred_on,
        "cost": cost,
        "service_mean": service_mean,
        "z_score": z_score,
        "severity": "critical" if abs(z_score) >= 3 else "warning",
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
                service=service,
                occurred_on=occurred_on,
                payload_json=json.dumps(payload),
            )
            conn.execute(
                "UPDATE events SET analysis_json = ? WHERE id = ?",
                (json.dumps(envelope), event_id),
            )
        return event_id
    finally:
        conn.close()


def _assert_recommendation_contract(body: dict) -> None:
    # confidence is a real probability on every lane (models don't bound it)
    assert 0.0 <= body["confidence"]["score"] <= 1.0
    # exactly the two stances, and a preferred that is one of them
    assert [option["stance"] for option in body["options"]] == ["CAUTIOUS", "BOLD"]
    assert body["preferred"] in ("CAUTIOUS", "BOLD")
    for option in body["options"]:
        assert option["risk"] in ("low", "medium", "high")
        assert option["title"].strip()  # never blank
        assert option["description"].strip()
        assert option["rollback"].strip()  # a reversible plan is always stated
        assert option["estimated_monthly_saving"] >= 0.0  # money never negative
    savings = body["savings"]
    assert savings["daily_excess"] >= 0.0
    assert savings["cautious_monthly"] >= 0.0
    assert savings["bold_monthly"] >= 0.0


def test_fake_debate_path_honors_the_contract(client):
    event_id = _seed_analyzed_event()
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    assert body["source"] in ("fake", "gemini")
    _assert_recommendation_contract(body)


def test_fallback_path_honors_the_contract(client, monkeypatch):
    monkeypatch.setattr(recommender, "get_provider", lambda: _UnavailableProvider())
    event_id = _seed_analyzed_event()
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    assert body["source"] == "fallback"
    assert body["model"] == "rule-based"  # the fallback never claims Gemini
    _assert_recommendation_contract(body)


def test_downward_anomaly_keeps_money_non_negative(client, monkeypatch):
    """A spend DROP must not produce a negative saving on any field — the
    clamp holds even when the fallback narrates verification, not savings."""
    monkeypatch.setattr(recommender, "get_provider", lambda: _UnavailableProvider())
    event_id = _seed_analyzed_event(z_score=-2.5, cost=10.0, service_mean=100.0)
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    _assert_recommendation_contract(body)
    assert body["savings"]["daily_excess"] == 0.0
