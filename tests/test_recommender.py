"""Tests for the Recommender agent + debate-lite (WP-4).

Acceptance criteria: a proposed inbox card with two options and computed
savings, debate-lite only on low confidence or analyst disagreement
(one extra call at most), a transcript when the Skeptic runs, and the
frozen prompt interface reserving the decision-memory slot.
"""

import inspect
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import db, recommender
from app.llm import FakeProvider, LLMResult, LLMUnavailableError
from main import app

HIGH_CONF_REPORT = {
    "category": "RIGHTSIZING",
    "options": [
        {
            "stance": "CAUTIOUS",
            "title": "stage a reversible resize",
            "description": "resize behind a window",
            "risk": "low",
            "rollback": "restore prior capacity",
        },
        {
            "stance": "BOLD",
            "title": "resize now",
            "description": "contain immediately",
            "risk": "high",
            "rollback": "reapply prior profile",
        },
    ],
    "preferred": "BOLD",
    "confidence": {"score": 0.9, "rationale": "clear overshoot"},
}

SKEPTIC_DISAGREES = {
    "agree": False,
    "preferred": "CAUTIOUS",
    "rationale": "the analysis does not justify the bold path",
}


class SchemaAwareProvider(FakeProvider):
    """Returns a canned payload per response schema; records calls + prompts."""

    def __init__(self, payloads: dict):
        super().__init__()
        self.payloads = payloads
        self.calls = []
        self.prompts = []

    def generate(self, prompt, *, system_instruction=None, response_schema=None):
        name = response_schema.__name__ if response_schema else None
        self.calls.append(name)
        self.prompts.append(prompt)
        payload = self.payloads[name]
        return LLMResult(
            text="stub",
            parsed=response_schema.model_validate(payload),
            source="fake",
            model="stub",
        )

    @property
    def model(self):
        return "stub"


class UnavailableProvider(FakeProvider):
    def generate(self, prompt, **kwargs):
        raise LLMUnavailableError("daily quota exhausted")

    @property
    def model(self):
        return "gemini-2.5-flash"


class SkepticExplodesProvider(FakeProvider):
    """Recommender call succeeds; the skeptic dies with a NON-quota error."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def generate(self, prompt, **kwargs):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("400 INVALID_ARGUMENT: schema rejected")
        return super().generate(prompt, **kwargs)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def seed_analyzed_event(
    *,
    triage: str = "REAL",
    z_score: float = 3.5,
    service: str = "compute",
    occurred_on: str = "2026-07-11",
    analyzed: bool = True,
    cost: float = 512.0,
    service_mean: float = 128.0,
) -> int:
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
            "triage": triage,
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
            if analyzed:
                conn.execute(
                    "UPDATE events SET analysis_json = ? WHERE id = ?",
                    (json.dumps(envelope), event_id),
                )
        return event_id
    finally:
        conn.close()


# --- endpoint contract ---------------------------------------------------------


def test_recommend_unknown_event_is_404(client):
    assert client.post("/anomalies/999/recommend").status_code == 404


def test_recommend_requires_prior_analysis(client):
    event_id = seed_analyzed_event(analyzed=False)
    response = client.post(f"/anomalies/{event_id}/recommend")
    assert response.status_code == 409
    assert "analyze" in response.json()["detail"]


def test_recommend_non_anomaly_event_is_409(client):
    conn = db.connect()
    try:
        with db.writing(conn):
            event_id = db.upsert_event(
                conn,
                kind="security",
                service="iam",
                occurred_on="2026-07-11",
                payload_json="{}",
            )
    finally:
        conn.close()
    assert client.post(f"/anomalies/{event_id}/recommend").status_code == 409


# --- recommendation content -----------------------------------------------------


def test_recommendation_files_a_proposed_action(client):
    event_id = seed_analyzed_event()
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    assert body["action_state"] == "proposed"
    assert body["event_id"] == event_id
    stances = [option["stance"] for option in body["options"]]
    assert stances == ["CAUTIOUS", "BOLD"]

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM actions WHERE id = ?", (body["action_id"],)
        ).fetchone()
    finally:
        conn.close()
    assert row["state"] == "proposed"
    assert row["event_id"] == event_id
    detail = json.loads(row["detail_json"])
    assert detail["preferred"] == body["preferred"]
    # the WP-5b single-screen evidence pack: every ingredient must be there
    assert {
        "category",
        "preferred",
        "options",
        "savings",
        "confidence",
        "escalation_reason",
        "transcript",
        "source",
        "model",
        "analysis",
        "anomaly",
    } <= set(detail)
    assert detail["analysis"]["triage"] == "REAL"
    assert detail["anomaly"]["service"] == "compute"
    assert body["model"] == "fake"  # honest attribution on the fake path


def test_savings_are_computed_in_python_not_by_the_model(client):
    event_id = seed_analyzed_event()  # cost 512, mean 128 -> excess 384
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    assert body["savings"]["daily_excess"] == 384.0
    assert body["savings"]["cautious_monthly"] == 4032.0  # 384 * 30 * 0.35
    assert body["savings"]["bold_monthly"] == 8064.0  # 384 * 30 * 0.70
    by_stance = {o["stance"]: o["estimated_monthly_saving"] for o in body["options"]}
    assert by_stance == {"CAUTIOUS": 4032.0, "BOLD": 8064.0}


def test_fake_provider_low_confidence_triggers_debate(client):
    """FakeProvider confidence (0.5) sits under the 0.6 debate threshold."""
    event_id = seed_analyzed_event()
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    assert body["escalation_reason"] is not None
    assert "low confidence" in body["escalation_reason"]
    assert body["transcript"] is not None
    assert body["transcript"]["trigger"] == body["escalation_reason"]

    conn = db.connect()
    try:
        agents = [
            row["agent"]
            for row in conn.execute("SELECT agent FROM ai_usage ORDER BY id")
        ]
    finally:
        conn.close()
    assert agents == ["recommender", "skeptic"]


def test_high_confidence_real_triage_skips_debate(client, monkeypatch):
    provider = SchemaAwareProvider({"RecommenderReport": HIGH_CONF_REPORT})
    monkeypatch.setattr(recommender, "get_provider", lambda: provider)
    event_id = seed_analyzed_event(triage="REAL")
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    assert provider.calls == ["RecommenderReport"]  # exactly one call
    assert body["escalation_reason"] is None
    assert body["transcript"] is None
    assert body["preferred"] == "BOLD"


def test_model_options_are_preserved_not_replaced_by_templates(client, monkeypatch):
    """When the model produces both stances, ITS narrative must reach the
    operator — a mutant swapping in the rule-based templates must fail."""
    provider = SchemaAwareProvider({"RecommenderReport": HIGH_CONF_REPORT})
    monkeypatch.setattr(recommender, "get_provider", lambda: provider)
    event_id = seed_analyzed_event(triage="REAL")
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    titles = {o["stance"]: o["title"] for o in body["options"]}
    assert titles == {
        "CAUTIOUS": "stage a reversible resize",
        "BOLD": "resize now",
    }
    assert body["category"] == "RIGHTSIZING"


def test_missing_stance_is_filled_from_templates_without_losing_the_other(client, monkeypatch):
    """A single-option model answer keeps that option and gets the missing
    stance from the deterministic templates."""
    one_sided = {
        **HIGH_CONF_REPORT,
        "options": [HIGH_CONF_REPORT["options"][1]],  # BOLD only
    }
    provider = SchemaAwareProvider({"RecommenderReport": one_sided})
    monkeypatch.setattr(recommender, "get_provider", lambda: provider)
    event_id = seed_analyzed_event(triage="REAL")
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    titles = {o["stance"]: o["title"] for o in body["options"]}
    assert titles["BOLD"] == "resize now"  # the model's option survived
    assert "low-traffic window" in titles["CAUTIOUS"]  # template filled in


def test_disagreement_triggers_debate_and_skeptic_can_flip(client, monkeypatch):
    provider = SchemaAwareProvider(
        {"RecommenderReport": HIGH_CONF_REPORT, "SkepticVerdict": SKEPTIC_DISAGREES}
    )
    monkeypatch.setattr(recommender, "get_provider", lambda: provider)
    event_id = seed_analyzed_event(triage="SEASONAL")
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    assert provider.calls == ["RecommenderReport", "SkepticVerdict"]
    assert "disagreement" in body["escalation_reason"]
    assert body["transcript"]["agreed"] is False
    assert body["transcript"]["original_preferred"] == "BOLD"
    assert body["preferred"] == "CAUTIOUS"  # the skeptic flipped the stance


def test_skeptic_agreement_never_flips_the_stance(client, monkeypatch):
    """agree=true with a different preferred field is advisory noise: the
    recommender's stance stands, and the transcript says so."""
    provider = SchemaAwareProvider(
        {
            "RecommenderReport": HIGH_CONF_REPORT,  # preferred BOLD
            "SkepticVerdict": {
                "agree": True,
                "preferred": "CAUTIOUS",
                "rationale": "either works; no objection",
            },
        }
    )
    monkeypatch.setattr(recommender, "get_provider", lambda: provider)
    event_id = seed_analyzed_event(triage="SEASONAL")  # disagreement debate
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    assert body["preferred"] == "BOLD"  # not flipped
    assert body["transcript"]["agreed"] is True
    assert body["transcript"]["final_preferred"] == "BOLD"


def test_skeptic_nonquota_failure_keeps_the_draft(client, monkeypatch):
    """Locked mirror of the analyst rule: ANY skeptic failure degrades to
    'draft kept' — the recommender call must still reach the ledger,
    the cache, and the inbox."""
    provider = SkepticExplodesProvider()
    monkeypatch.setattr(recommender, "get_provider", lambda: provider)
    event_id = seed_analyzed_event()  # fake confidence 0.5 -> debate fires

    response = client.post(f"/anomalies/{event_id}/recommend")
    assert response.status_code == 200
    body = response.json()
    assert provider.calls == 2  # the skeptic was attempted
    assert body["escalation_reason"].endswith("(skeptic unavailable — draft kept)")
    assert body["transcript"] is None
    assert body["action_state"] == "proposed"

    conn = db.connect()
    try:
        agents = [r["agent"] for r in conn.execute("SELECT agent FROM ai_usage")]
        cached = conn.execute("SELECT count(*) FROM llm_cache").fetchone()[0]
    finally:
        conn.close()
    assert agents == ["recommender"]  # no phantom skeptic row
    assert cached == 1


def test_skeptic_ledger_row_hashes_the_skeptic_prompt(client, monkeypatch):
    """Debate provenance: each ai_usage row must hash the prompt that was
    ACTUALLY sent for that call — not merely differ from each other."""
    import hashlib

    provider = SchemaAwareProvider(
        {
            "RecommenderReport": {
                **HIGH_CONF_REPORT,
                "confidence": {"score": 0.3, "rationale": "unsure"},
            },
            "SkepticVerdict": SKEPTIC_DISAGREES,
        }
    )
    monkeypatch.setattr(recommender, "get_provider", lambda: provider)
    event_id = seed_analyzed_event()
    client.post(f"/anomalies/{event_id}/recommend")

    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT agent, prompt_sha256 FROM ai_usage ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    by_agent = {row["agent"]: row["prompt_sha256"] for row in rows}
    assert len(provider.prompts) == 2
    expected = {
        "recommender": hashlib.sha256(provider.prompts[0].encode()).hexdigest(),
        "skeptic": hashlib.sha256(provider.prompts[1].encode()).hexdigest(),
    }
    assert by_agent == expected


def test_debate_is_at_most_one_extra_call(client, monkeypatch):
    """Low confidence AND disagreement together still cost one skeptic call."""
    provider = SchemaAwareProvider(
        {
            "RecommenderReport": {**HIGH_CONF_REPORT, "confidence": {"score": 0.3, "rationale": "unsure"}},
            "SkepticVerdict": {**SKEPTIC_DISAGREES, "agree": True, "preferred": "BOLD"},
        }
    )
    monkeypatch.setattr(recommender, "get_provider", lambda: provider)
    event_id = seed_analyzed_event(triage="SEASONAL")
    client.post(f"/anomalies/{event_id}/recommend")
    assert provider.calls == ["RecommenderReport", "SkepticVerdict"]


# --- idempotent re-recommend ------------------------------------------------------


def test_second_recommend_reuses_the_open_action(client):
    event_id = seed_analyzed_event()
    first = client.post(f"/anomalies/{event_id}/recommend").json()

    conn = db.connect()
    try:
        usage_after_first = conn.execute("SELECT count(*) FROM ai_usage").fetchone()[0]
    finally:
        conn.close()

    second = client.post(f"/anomalies/{event_id}/recommend").json()
    assert second["reused"] is True
    assert second["action_id"] == first["action_id"]

    conn = db.connect()
    try:
        count = conn.execute("SELECT count(*) FROM actions").fetchone()[0]
        usage_after_second = conn.execute("SELECT count(*) FROM ai_usage").fetchone()[0]
    finally:
        conn.close()
    assert count == 1
    # the reused path makes no provider call and must not ledger phantoms
    assert usage_after_second == usage_after_first


@pytest.mark.parametrize("open_state", ["approved", "executed"])
def test_reuse_lane_covers_every_non_rejected_state(client, open_state):
    """The locked rule says any OPEN action blocks a new card — not just a
    proposed one. An approved or already-executed action must be reused."""
    event_id = seed_analyzed_event()
    first = client.post(f"/anomalies/{event_id}/recommend").json()
    conn = db.connect()
    try:
        with db.writing(conn):
            conn.execute(
                "UPDATE actions SET state = ? WHERE id = ?",
                (open_state, first["action_id"]),
            )
    finally:
        conn.close()

    second = client.post(f"/anomalies/{event_id}/recommend").json()
    assert second["reused"] is True
    assert second["action_id"] == first["action_id"]
    assert second["action_state"] == open_state

    conn = db.connect()
    try:
        count = conn.execute("SELECT count(*) FROM actions").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_downward_anomaly_fallback_narrative_matches_the_direction(client, monkeypatch):
    """A spend DROP served by the fallback must read as verification and
    escalation — never as 'contain the overspend' with zero savings."""
    monkeypatch.setattr(recommender, "get_provider", lambda: UnavailableProvider())
    event_id = seed_analyzed_event(z_score=-2.5, cost=10.0, service_mean=100.0)
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    titles = {o["stance"]: o["title"] for o in body["options"]}
    assert "Verify billing and ingestion" in titles["CAUTIOUS"]
    assert "spend drop" in titles["BOLD"]
    assert body["savings"]["daily_excess"] == 0.0
    assert body["savings"]["cautious_monthly"] == 0.0
    descriptions = " ".join(o["description"] for o in body["options"])
    assert "overspend" not in descriptions.lower()


def test_timeout_expired_proposal_re_recommends_from_cache(client):
    """The one production lane where the recommender cache replays: a
    system:timeout rejection records NO decision (memory holds human
    intent only), so the prompt is unchanged — the replay must come from
    llm_cache with the debate outcome verbatim, be ledgered from_cache=1,
    and never re-run the skeptic."""
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
    swept = client.get("/actions").json()["actions"]
    assert swept[0]["state"] == "rejected"
    assert swept[0]["decided_by"] == "system:timeout"

    second = client.post(f"/anomalies/{event_id}/recommend").json()
    assert second["reused"] is False
    assert second["from_cache"] is True
    assert second["transcript"] == first["transcript"]
    assert second["escalation_reason"] == first["escalation_reason"]
    assert second["preferred"] == first["preferred"]

    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT agent, from_cache FROM ai_usage ORDER BY id"
        ).fetchall()
        decisions = conn.execute("SELECT count(*) FROM decisions").fetchone()[0]
    finally:
        conn.close()
    recommender_rows = [r["from_cache"] for r in rows if r["agent"] == "recommender"]
    skeptic_rows = [r for r in rows if r["agent"] == "skeptic"]
    assert recommender_rows == [0, 1]  # locked rule: cache hits reach the ledger
    assert len(skeptic_rows) == 1  # the replay never re-debates
    assert decisions == 0  # the timeout really recorded nothing


def test_concurrent_recommends_file_exactly_one_action(client):
    """Two racing recommends: the in-transaction re-check under BEGIN
    IMMEDIATE must collapse them onto one inbox card."""
    event_id = seed_analyzed_event()
    holder = db.connect()
    holder.execute("BEGIN IMMEDIATE")
    try:
        with ThreadPoolExecutor(2) as pool:
            futures = [
                pool.submit(
                    lambda: client.post(f"/anomalies/{event_id}/recommend")
                )
                for _ in range(2)
            ]
            time.sleep(0.5)  # both requests are parked on the write lock
            holder.commit()
            responses = [f.result() for f in futures]
    finally:
        holder.close()

    assert [r.status_code for r in responses] == [200, 200]
    assert len({r.json()["action_id"] for r in responses}) == 1
    conn = db.connect()
    try:
        count = conn.execute("SELECT count(*) FROM actions").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_rejected_action_allows_a_fresh_recommendation(client):
    event_id = seed_analyzed_event()
    first = client.post(f"/anomalies/{event_id}/recommend").json()
    client.post(f"/actions/{first['action_id']}/reject")
    second = client.post(f"/anomalies/{event_id}/recommend").json()
    assert second["reused"] is False
    assert second["action_id"] != first["action_id"]
    # WP-6: the rejection itself became decision memory, so the fresh
    # recommendation reasons over a DIFFERENT prompt — a cache replay here
    # would resurface the pre-rejection reasoning and hide the new context.
    # (Cache-hit ledger semantics stay pinned by the analyst suite, which
    # shares the same cache/ledger helpers.)
    assert second["from_cache"] is False
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT agent, from_cache FROM ai_usage ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    recommender_rows = [r["from_cache"] for r in rows if r["agent"] == "recommender"]
    assert recommender_rows == [0, 0]  # both runs reasoned fresh


# --- fallback ----------------------------------------------------------------------


def test_fallback_recommendation_still_files_an_action(client, monkeypatch):
    monkeypatch.setattr(recommender, "get_provider", lambda: UnavailableProvider())
    event_id = seed_analyzed_event()
    body = client.post(f"/anomalies/{event_id}/recommend").json()
    assert body["source"] == "fallback"
    assert body["model"] == "rule-based"  # never masquerades as Gemini
    assert body["action_state"] == "proposed"
    assert [o["stance"] for o in body["options"]] == ["CAUTIOUS", "BOLD"]
    assert body["preferred"] == "CAUTIOUS"
    # the actual trigger is pinned, not just the suffix
    assert body["escalation_reason"].startswith("low confidence (0.40 < 0.60)")
    assert body["escalation_reason"].endswith("(skeptic skipped on fallback)")
    assert body["transcript"] is None

    conn = db.connect()
    try:
        cached = conn.execute("SELECT count(*) FROM llm_cache").fetchone()[0]
    finally:
        conn.close()
    assert cached == 0  # fallback answers are never cached


# --- frozen prompt interface --------------------------------------------------------


def test_prompt_interface_reserves_the_decision_memory_slot():
    """WP-6 injects prior decisions via this exact frozen signature."""
    signature = inspect.signature(recommender.build_prompt)
    assert list(signature.parameters) == [
        "anomaly",
        "analyst_report",
        "savings",
        "decision_memory",
    ]
    assert signature.parameters["decision_memory"].default == ""
    with_memory = recommender.build_prompt({}, {}, {}, "operator approved before")
    without_memory = recommender.build_prompt({}, {}, {})
    assert "operator approved before" in with_memory
    assert "Prior operator decisions" in with_memory
    assert "Prior operator decisions" not in without_memory
    # the memory block is untrusted operator text: it must sit INSIDE the
    # spotlighting delimiters, not float free next to the instructions
    from app.llm import DATA_DELIMITER_CLOSE, DATA_DELIMITER_OPEN

    memory_segment = with_memory.split("Prior operator decisions on similar signals:")[1]
    assert memory_segment.strip().startswith(DATA_DELIMITER_OPEN)
    assert DATA_DELIMITER_CLOSE in memory_segment
    open_index = memory_segment.index(DATA_DELIMITER_OPEN)
    close_index = memory_segment.index(DATA_DELIMITER_CLOSE)
    assert open_index < memory_segment.index("operator approved before") < close_index
