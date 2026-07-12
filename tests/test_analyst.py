"""Tests for the Analyst agent and POST /anomalies/{id}/analyze (WP-3).

All provider interaction runs through FakeProvider (deterministic) or
purpose-built stubs; acceptance criteria: stable anomaly ids across
rescans, consistent reports for the planted anomalies, reflection only
at |z| >= 3, cache discipline, and the rule-based fallback path.
"""

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app import analyst, db
from app.llm import FakeProvider, LLMResult, LLMUnavailableError
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


class CountingProvider(FakeProvider):
    """FakeProvider that counts generate() calls for quota assertions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = 0

    def generate(self, prompt, **kwargs):
        self.calls += 1
        return super().generate(prompt, **kwargs)


class UnavailableProvider(FakeProvider):
    """Simulates an exhausted quota on every call."""

    def generate(self, prompt, **kwargs):
        raise LLMUnavailableError("daily quota exhausted")

    @property
    def model(self):
        return "gemini-2.5-flash"


class RecordingProvider(FakeProvider):
    """Records every prompt and can serve per-call canned payloads."""

    def __init__(self, payloads_by_call=None):
        super().__init__()
        self.prompts = []
        self.payloads_by_call = payloads_by_call or []

    def generate(self, prompt, *, system_instruction=None, response_schema=None):
        self.prompts.append(prompt)
        index = len(self.prompts) - 1
        if index < len(self.payloads_by_call):
            return LLMResult(
                text="stub",
                parsed=response_schema.model_validate(self.payloads_by_call[index]),
                source="fake",
                model="fake",
            )
        return super().generate(
            prompt, system_instruction=system_instruction, response_schema=response_schema
        )


class ReflectionExplodesProvider(FakeProvider):
    """Draft succeeds; the reflection call dies with a NON-quota error."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def generate(self, prompt, **kwargs):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("400 INVALID_ARGUMENT: request too large")
        return super().generate(prompt, **kwargs)


class FirstCallQuotaProvider(FakeProvider):
    """Quota dies on the first call only — kills a reflect-on-fallback mutant."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def generate(self, prompt, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise LLMUnavailableError("quota")
        return super().generate(prompt, **kwargs)


def seed_anomaly_event(z_score: float, service: str = "compute") -> int:
    """Persist a synthetic anomaly event, the way the scan endpoint does."""
    payload = {
        "service": service,
        "date": "2026-07-11",
        "cost": 512.0,
        "service_mean": 128.0,
        "z_score": z_score,
        "severity": "critical" if abs(z_score) >= 3 else "warning",
    }
    conn = db.connect()
    try:
        with db.writing(conn):
            return db.upsert_event(
                conn,
                kind="cost_anomaly",
                service=service,
                occurred_on=payload["date"],
                payload_json=json.dumps(payload),
            )
    finally:
        conn.close()


# --- stable ids on the scan endpoint -----------------------------------------


def test_scan_assigns_ids_and_keeps_them_stable(client):
    first = client.get("/anomalies").json()["anomalies"]
    second = client.get("/anomalies").json()["anomalies"]
    assert first, "the planted anomalies must be detected"
    assert all(a["id"] is not None for a in first)
    assert [a["id"] for a in first] == [a["id"] for a in second]
    assert len({a["id"] for a in first}) == len(first)


def test_scan_ids_survive_threshold_changes(client):
    """Signals minted by a strict scan keep their ids when a looser scan
    later widens the set — the direction that would mint churn if the
    upsert were a plain insert-or-replace."""
    strict = {
        (a["service"], a["date"]): a["id"]
        for a in client.get("/anomalies", params={"threshold": 3.0}).json()["anomalies"]
    }
    assert strict, "the planted criticals must be detected at z>=3"
    loose = client.get("/anomalies", params={"threshold": 2.0}).json()["anomalies"]
    assert len(loose) >= len(strict)
    loose_ids = {(a["service"], a["date"]): a["id"] for a in loose}
    for key, minted_id in strict.items():
        assert loose_ids[key] == minted_id
    assert len(set(loose_ids.values())) == len(loose_ids)


def test_service_filter_keeps_ids(client):
    everything = client.get("/anomalies").json()["anomalies"]
    target = everything[0]["service"]
    filtered = client.get("/anomalies", params={"service": target}).json()["anomalies"]
    assert filtered
    ids = {a["id"] for a in everything if a["service"] == target}
    assert {a["id"] for a in filtered} == ids


# --- endpoint contract ---------------------------------------------------------


def test_analyze_unknown_event_is_404(client):
    assert client.post("/anomalies/999/analyze").status_code == 404


def test_analyze_out_of_range_id_is_422(client):
    assert client.post(f"/anomalies/{2**63}/analyze").status_code == 422


def test_analyze_non_anomaly_event_is_409(client):
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
    response = client.post(f"/anomalies/{event_id}/analyze")
    assert response.status_code == 409
    assert "'security'" in response.json()["detail"]


# --- analysis behavior ----------------------------------------------------------


def test_analyze_returns_deterministic_fake_report(client):
    event_id = seed_anomaly_event(z_score=2.4)
    response = client.post(f"/anomalies/{event_id}/analyze")
    assert response.status_code == 200
    body = response.json()
    assert body["event_id"] == event_id
    assert body["triage"] == "REAL"  # FakeProvider picks the first Literal
    assert body["confidence"]["score"] == 0.5
    assert body["source"] == "fake"
    assert body["model"] == "fake"
    assert body["reflected"] is False
    assert body["from_cache"] is False


def test_analysis_is_persisted_on_the_event(client):
    event_id = seed_anomaly_event(z_score=2.4)
    client.post(f"/anomalies/{event_id}/analyze")
    conn = db.connect()
    try:
        stored = conn.execute(
            "SELECT analysis_json FROM events WHERE id = ?", (event_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    envelope = json.loads(stored)
    assert envelope["report"]["triage"] == "REAL"
    assert envelope["source"] == "fake"


def test_reflection_runs_only_at_critical_z(client, monkeypatch):
    provider = CountingProvider()
    monkeypatch.setattr(analyst, "get_provider", lambda: provider)

    warning_event = seed_anomaly_event(z_score=2.2, service="storage")
    body = client.post(f"/anomalies/{warning_event}/analyze").json()
    assert provider.calls == 1
    assert body["reflected"] is False

    critical_event = seed_anomaly_event(z_score=5.0, service="network")
    body = client.post(f"/anomalies/{critical_event}/analyze").json()
    assert provider.calls == 3  # draft + reflection
    assert body["reflected"] is True


def test_second_analysis_is_served_from_cache(client, monkeypatch):
    provider = CountingProvider()
    monkeypatch.setattr(analyst, "get_provider", lambda: provider)
    event_id = seed_anomaly_event(z_score=2.2)

    first = client.post(f"/anomalies/{event_id}/analyze").json()
    second = client.post(f"/anomalies/{event_id}/analyze").json()

    assert provider.calls == 1  # the replay made no provider call
    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert second["triage"] == first["triage"]

    conn = db.connect()
    try:
        usage = conn.execute(
            "SELECT from_cache FROM ai_usage WHERE agent = 'analyst' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert [row["from_cache"] for row in usage] == [0, 1]


def test_fallback_answers_when_llm_unavailable_and_is_not_cached(client, monkeypatch):
    monkeypatch.setattr(analyst, "get_provider", lambda: UnavailableProvider())
    event_id = seed_anomaly_event(z_score=5.0)

    body = client.post(f"/anomalies/{event_id}/analyze").json()
    assert body["source"] == "fallback"
    assert body["triage"] == "REAL"  # |z| >= 3 rule
    assert body["reflected"] is False  # reflection never runs on fallback

    conn = db.connect()
    try:
        cached = conn.execute("SELECT count(*) FROM llm_cache").fetchone()[0]
        usage = conn.execute(
            "SELECT source FROM ai_usage WHERE agent = 'analyst'"
        ).fetchall()
    finally:
        conn.close()
    assert cached == 0  # a quota blip must not poison the cache
    assert [row["source"] for row in usage] == ["fallback"]


def test_fallback_below_critical_is_seasonal(client, monkeypatch):
    monkeypatch.setattr(analyst, "get_provider", lambda: UnavailableProvider())
    event_id = seed_anomaly_event(z_score=2.1)
    body = client.post(f"/anomalies/{event_id}/analyze").json()
    assert body["triage"] == "SEASONAL"
    assert body["confidence"]["score"] == 0.4


def test_evidence_rows_enumerate_the_service_history():
    evidence = analyst.build_evidence("compute")
    assert evidence, "the mock dataset has compute records"
    assert evidence[0]["eid"] == "E1"
    assert len(evidence) <= analyst.EVIDENCE_WINDOW_DAYS
    assert [row["eid"] for row in evidence] == [
        f"E{i + 1}" for i in range(len(evidence))
    ]


def test_analyze_zero_id_is_422(client):
    assert client.post("/anomalies/0/analyze").status_code == 422


def test_reflection_failure_keeps_the_draft(client, monkeypatch):
    """Locked decision: reflection is best-effort for ANY error, not just
    quota errors — the paid-for draft must survive, reach the ledger and
    the cache, and the request must stay 200."""
    provider = ReflectionExplodesProvider()
    monkeypatch.setattr(analyst, "get_provider", lambda: provider)
    event_id = seed_anomaly_event(z_score=5.0)

    response = client.post(f"/anomalies/{event_id}/analyze")
    assert response.status_code == 200
    body = response.json()
    assert body["reflected"] is False
    assert body["triage"] == "REAL"  # the draft
    assert provider.calls == 2  # reflection was attempted

    conn = db.connect()
    try:
        agents = [r["agent"] for r in conn.execute("SELECT agent FROM ai_usage")]
        cached = conn.execute("SELECT count(*) FROM llm_cache").fetchone()[0]
    finally:
        conn.close()
    assert agents == ["analyst"]  # no phantom reflection row
    assert cached == 1  # the draft is cached


def test_reflection_uses_its_own_prompt_and_result(client, monkeypatch):
    """The reflection call must send the reflection prompt (not resend the
    draft prompt), its result must become the final report, and the
    ledger row must carry the reflection prompt's hash."""
    draft = {
        "triage": "SEASONAL",
        "summary": "draft summary",
        "probable_cause": "draft cause",
        "evidence_ids": ["E1"],
        "confidence": {"score": 0.7, "rationale": "draft"},
    }
    reflected = {**draft, "triage": "DATA_ERROR", "summary": "reflected summary"}
    provider = RecordingProvider(payloads_by_call=[draft, reflected])
    monkeypatch.setattr(analyst, "get_provider", lambda: provider)
    event_id = seed_anomaly_event(z_score=5.0)

    body = client.post(f"/anomalies/{event_id}/analyze").json()
    assert body["reflected"] is True
    assert body["triage"] == "DATA_ERROR"  # the reflection result won
    assert len(provider.prompts) == 2
    assert provider.prompts[1] != provider.prompts[0]
    assert provider.prompts[1].startswith("Review this draft analysis")

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT prompt_sha256 FROM ai_usage WHERE agent = 'analyst-reflection'"
        ).fetchone()
    finally:
        conn.close()
    expected = hashlib.sha256(provider.prompts[1].encode("utf-8")).hexdigest()
    assert row["prompt_sha256"] == expected


def test_reflection_never_runs_after_fallback(client, monkeypatch):
    """A provider that recovers right after the draft's quota failure must
    NOT get a reflection call for a fallback answer."""
    provider = FirstCallQuotaProvider()
    monkeypatch.setattr(analyst, "get_provider", lambda: provider)
    event_id = seed_anomaly_event(z_score=5.0)

    body = client.post(f"/anomalies/{event_id}/analyze").json()
    assert body["source"] == "fallback"
    assert body["reflected"] is False
    assert provider.calls == 1  # no second call for a rule-based answer


def test_fallback_is_attributed_to_rule_based_model(client, monkeypatch):
    """A fallback must never masquerade as a live model in the response,
    the persisted envelope, or the ai_usage ledger."""
    monkeypatch.setattr(analyst, "get_provider", lambda: UnavailableProvider())
    event_id = seed_anomaly_event(z_score=5.0)

    body = client.post(f"/anomalies/{event_id}/analyze").json()
    assert body["model"] == "rule-based"

    conn = db.connect()
    try:
        envelope = json.loads(
            conn.execute(
                "SELECT analysis_json FROM events WHERE id = ?", (event_id,)
            ).fetchone()[0]
        )
        usage_model = conn.execute(
            "SELECT model FROM ai_usage WHERE agent = 'analyst'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert envelope["model"] == "rule-based"
    assert usage_model == "rule-based"


def test_hallucinated_evidence_ids_are_dropped(client, monkeypatch):
    report = {
        "triage": "REAL",
        "summary": "s",
        "probable_cause": "p",
        "evidence_ids": ["E1", "E99", "totally-made-up"],
        "confidence": {"score": 0.8, "rationale": "r"},
    }
    provider = RecordingProvider(payloads_by_call=[report])
    monkeypatch.setattr(analyst, "get_provider", lambda: provider)
    event_id = seed_anomaly_event(z_score=2.2)  # no reflection

    body = client.post(f"/anomalies/{event_id}/analyze").json()
    assert body["evidence_ids"] == ["E1"]


def test_critical_cache_replay_preserves_the_reflected_flag(client, monkeypatch):
    provider = CountingProvider()
    monkeypatch.setattr(analyst, "get_provider", lambda: provider)
    event_id = seed_anomaly_event(z_score=5.0)

    first = client.post(f"/anomalies/{event_id}/analyze").json()
    second = client.post(f"/anomalies/{event_id}/analyze").json()
    assert provider.calls == 2  # draft + reflection, then pure replay
    assert first["reflected"] is True
    assert second["reflected"] is True
    assert second["from_cache"] is True


def test_rescan_does_not_clobber_a_stored_analysis(client):
    anomalies = client.get("/anomalies").json()["anomalies"]
    event_id = anomalies[0]["id"]
    client.post(f"/anomalies/{event_id}/analyze")
    client.get("/anomalies")  # rescan refreshes payloads via the upsert

    conn = db.connect()
    try:
        stored = conn.execute(
            "SELECT analysis_json FROM events WHERE id = ?", (event_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert stored is not None
    assert json.loads(stored)["report"]["triage"]


def test_evidence_window_keeps_only_the_latest_fourteen(monkeypatch):
    records = [
        {"service": "compute", "date": f"2026-06-{day:02d}", "cost": float(day)}
        for day in range(1, 21)  # 20 days: window must truncate
    ]
    monkeypatch.setattr(analyst, "load_daily_costs", lambda: records)
    evidence = analyst.build_evidence("compute")
    assert len(evidence) == analyst.EVIDENCE_WINDOW_DAYS
    assert evidence[0]["date"] == "2026-06-07"  # oldest six dropped
    assert evidence[-1]["date"] == "2026-06-20"


def test_scan_then_analyze_end_to_end(client):
    """The acceptance path: detect → pick a planted anomaly → analyze it."""
    anomalies = client.get("/anomalies").json()["anomalies"]
    for anomaly in anomalies[:2]:  # both planted anomalies analyze consistently
        body = client.post(f"/anomalies/{anomaly['id']}/analyze").json()
        assert body["event_id"] == anomaly["id"]
        assert body["triage"] in {"REAL", "SEASONAL", "DATA_ERROR", "KNOWN_CHANGE"}
        assert 0.0 <= body["confidence"]["score"] <= 1.0
