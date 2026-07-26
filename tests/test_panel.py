"""Heterogeneous review panel — the debate ladder's critical rung.

test_recommender.py pins the warning rung (single skeptic, one extra
call); this suite pins the panel: a contested CRITICAL signal convenes
three adversarial reviewers whose majority decides the stance, dissent
and abstentions land on the record, failures abstain rather than vote,
and the cached envelope replays the panel verbatim without re-debating.

Acceptance criteria:
- fake lane: three deterministic personas with REAL dissent, ledgered
  as one row per answered seat (panel-<persona>);
- majority can overrule the draft; a tie or below-quorum keeps it;
- a dead reviewer abstains — no fabricated vote reaches the tally;
- warning signals never convene the panel.
"""

import pytest
from fastapi.testclient import TestClient

from app import db, recommender
from app.llm import (
    DEFAULT_PANEL_MODELS,
    FakeProvider,
    get_panel_providers,
    panel_models,
)
from main import app
from tests.test_recommender import (
    HIGH_CONF_REPORT,
    SchemaAwareProvider,
    seed_analyzed_event,
)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


class ExplodingProvider(FakeProvider):
    def generate(self, prompt, **kwargs):
        raise RuntimeError("500 backend hiccup")


def _ledger_agents() -> list[str]:
    conn = db.connect()
    try:
        return [
            row["agent"]
            for row in conn.execute("SELECT agent FROM ai_usage ORDER BY id")
        ]
    finally:
        conn.close()


# --- convening rules ---------------------------------------------------------


def test_critical_contested_call_convenes_the_panel(client):
    """Fake confidence (0.5) contests the call; critical severity promotes
    the debate from the single skeptic to the three-seat panel."""
    event_id = seed_analyzed_event()  # z 3.5 -> critical
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    transcript = body["transcript"]
    assert transcript is not None
    assert transcript["mode"] == "panel"
    assert len(transcript["reviewers"]) == 3
    assert transcript["trigger"] == body["escalation_reason"]
    assert set(transcript["votes"]) == {"CAUTIOUS", "BOLD"}
    agents = _ledger_agents()
    assert agents[0] == "recommender"
    assert {"panel-stability", "panel-throughput", "panel-evidence"} <= set(agents)
    assert "skeptic" not in agents  # the panel replaced the single seat


def test_warning_contested_call_keeps_the_single_skeptic(client):
    event_id = seed_analyzed_event(z_score=2.5)  # warning
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    transcript = body["transcript"]
    assert transcript is not None
    assert "reviewers" not in transcript
    assert "panel" not in {agent.split("-")[0] for agent in _ledger_agents()}


# --- fake personas: real dissent, deterministic --------------------------------


def test_throughput_persona_dissents_on_big_savings_cautious_draft(client):
    """The default fake draft prefers CAUTIOUS while the computed bold
    savings clear the throughput bar — one dissent on the record, the
    majority keeps the draft."""
    event_id = seed_analyzed_event()  # cost 512 vs mean 128 -> bold_monthly 8064
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    transcript = body["transcript"]
    assert transcript["agreed"] is True
    assert body["preferred"] == "CAUTIOUS"
    stances = {r["persona"]: r["agreed"] for r in transcript["reviewers"]}
    assert stances["throughput"] is False
    assert stances["stability"] is True and stances["evidence"] is True
    assert transcript["votes"] == {"CAUTIOUS": 2, "BOLD": 1}


def test_panel_majority_can_overrule_a_bold_draft(client, monkeypatch):
    """SEASONAL triage + BOLD draft on a critical signal: stability and
    evidence both dissent toward CAUTIOUS — the majority flips the stance."""
    provider = SchemaAwareProvider({"RecommenderReport": HIGH_CONF_REPORT})
    monkeypatch.setattr(recommender, "get_provider", lambda: provider)
    event_id = seed_analyzed_event(triage="SEASONAL")  # critical + disagreement
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    transcript = body["transcript"]
    assert transcript["agreed"] is False
    assert transcript["original_preferred"] == "BOLD"
    assert transcript["final_preferred"] == "CAUTIOUS"
    assert body["preferred"] == "CAUTIOUS"
    assert transcript["votes"] == {"CAUTIOUS": 2, "BOLD": 1}


# --- failure semantics ---------------------------------------------------------


def test_dead_reviewer_abstains_instead_of_voting(client, monkeypatch):
    monkeypatch.setattr(
        recommender,
        "get_panel_providers",
        lambda: [FakeProvider(), ExplodingProvider(), FakeProvider()],
    )
    event_id = seed_analyzed_event()
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    transcript = body["transcript"]
    reviewers = transcript["reviewers"]
    assert [r["stance"] is None for r in reviewers] == [False, True, False]
    assert reviewers[1]["argument"] == "unavailable — abstained"
    # quorum met by the two answered seats; the abstention never reaches
    # the ledger either
    assert "panel-throughput" not in _ledger_agents()


def test_all_reviewers_dead_keeps_the_draft(client, monkeypatch):
    monkeypatch.setattr(
        recommender,
        "get_panel_providers",
        lambda: [ExplodingProvider(), ExplodingProvider(), ExplodingProvider()],
    )
    event_id = seed_analyzed_event()
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    assert body["transcript"] is None
    assert body["escalation_reason"].endswith("(panel unavailable — draft kept)")
    assert body["action_state"] == "proposed"


# --- cache replay ---------------------------------------------------------------


def test_expired_replay_carries_the_panel_without_reconvening(client):
    event_id = seed_analyzed_event()
    first = client.post(f"/anomalies/{event_id}/recommend").json()
    conn = db.connect()
    try:
        with db.writing(conn):
            conn.execute(
                "UPDATE actions SET proposed_at = datetime('now', '-100 hours') "
                "WHERE id = ?",
                (first["action_id"],),
            )
    finally:
        conn.close()
    client.get("/actions")  # TTL sweep rejects the stale card
    second = client.post(f"/anomalies/{event_id}/recommend").json()
    assert second["from_cache"] is True
    assert second["transcript"] == first["transcript"]
    panel_rows = [a for a in _ledger_agents() if a.startswith("panel-")]
    assert len(panel_rows) == 3  # the replay never reconvenes the panel


# --- roster plumbing -------------------------------------------------------------


def test_panel_models_env_override_and_default(monkeypatch):
    monkeypatch.delenv("SENTINEL_PANEL_MODELS", raising=False)
    assert panel_models() == DEFAULT_PANEL_MODELS
    monkeypatch.setenv("SENTINEL_PANEL_MODELS", "gemini-a , gemini-b")
    assert panel_models() == ("gemini-a", "gemini-b")
    monkeypatch.setenv("SENTINEL_PANEL_MODELS", " , ")
    assert panel_models() == DEFAULT_PANEL_MODELS


def test_fake_mode_returns_fake_seats_only():
    providers = get_panel_providers()
    assert len(providers) == len(DEFAULT_PANEL_MODELS)
    assert all(isinstance(provider, FakeProvider) for provider in providers)
    assert all(provider.model == "fake" for provider in providers)
