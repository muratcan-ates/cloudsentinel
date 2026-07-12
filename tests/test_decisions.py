"""Tests for decision memory (WP-6).

Acceptance criterion from the sprint plan is retrieval-based: a recorded
operator verdict must be VISIBLE both through GET /decisions/similar and
inside the Recommender's prompt for the next similar signal.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import db, recommender
from tests.test_recommender import (
    HIGH_CONF_REPORT,
    SchemaAwareProvider,
    seed_analyzed_event,
)
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def decide_via_api(client, *, verdict: str, rationale: str | None, service: str, occurred_on: str):
    """Recommend on a fresh analyzed event, then decide it through the API."""
    event_id = seed_analyzed_event(service=service, occurred_on=occurred_on)
    action_id = client.post(f"/anomalies/{event_id}/recommend").json()["action_id"]
    body = {"actor": "tuana"}
    if rationale is not None:
        body["rationale"] = rationale
    response = client.post(f"/actions/{action_id}/{verdict}", json=body)
    assert response.status_code == 200
    return action_id


# --- recording ------------------------------------------------------------------


def test_operator_decisions_land_in_memory(client):
    action_id = decide_via_api(
        client,
        verdict="approve",
        rationale="idle capacity confirmed by the owning team",
        service="compute",
        occurred_on="2026-07-01",
    )
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM decisions").fetchone()
    finally:
        conn.close()
    assert row["action_id"] == action_id
    assert row["service"] == "compute"
    assert row["verdict"] == "approved"
    assert row["rationale"] == "idle capacity confirmed by the owning team"
    context = json.loads(row["input_context_json"])
    assert "preferred" in context  # the full evidence pack was snapshotted


def test_reject_records_a_decision_without_rationale(client):
    decide_via_api(
        client, verdict="reject", rationale=None, service="storage", occurred_on="2026-07-02"
    )
    conn = db.connect()
    try:
        row = conn.execute("SELECT verdict, rationale FROM decisions").fetchone()
    finally:
        conn.close()
    assert row["verdict"] == "rejected"
    assert row["rationale"] is None


def test_idempotent_replay_does_not_duplicate_memory(client):
    event_id = seed_analyzed_event()
    action_id = client.post(f"/anomalies/{event_id}/recommend").json()["action_id"]
    headers = {"Idempotency-Key": "decide-once"}
    assert client.post(f"/actions/{action_id}/approve", headers=headers).status_code == 200
    assert client.post(f"/actions/{action_id}/approve", headers=headers).status_code == 200
    conn = db.connect()
    try:
        count = conn.execute("SELECT count(*) FROM decisions").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_timeout_expiry_never_pollutes_memory(client):
    from tests.test_actions import seed_stale_proposal

    seed_stale_proposal(hours_old=100)
    client.get("/actions")  # sweep: stale proposal -> rejected by system:timeout
    conn = db.connect()
    try:
        count = conn.execute("SELECT count(*) FROM decisions").fetchone()[0]
    finally:
        conn.close()
    assert count == 0  # memory holds human intent only


# --- retrieval ------------------------------------------------------------------


def test_similar_returns_only_the_requested_service_newest_first(client):
    decide_via_api(client, verdict="approve", rationale="first", service="compute", occurred_on="2026-07-01")
    decide_via_api(client, verdict="reject", rationale="second", service="compute", occurred_on="2026-07-02")
    decide_via_api(client, verdict="approve", rationale="other", service="storage", occurred_on="2026-07-03")

    body = client.get("/decisions/similar", params={"service": "compute"}).json()
    assert body["service"] == "compute"
    assert body["count"] == 2
    assert [d["rationale"] for d in body["decisions"]] == ["second", "first"]
    assert all(d["service"] == "compute" for d in body["decisions"])


def test_similar_is_case_insensitive_and_empty_for_unknown(client):
    decide_via_api(client, verdict="approve", rationale="r", service="compute", occurred_on="2026-07-01")
    assert client.get("/decisions/similar", params={"service": "COMPUTE"}).json()["count"] == 1
    assert client.get("/decisions/similar", params={"service": "nonexistent"}).json()["count"] == 0


def test_similar_requires_the_service_parameter(client):
    assert client.get("/decisions/similar").status_code == 422


# --- injection into the frozen prompt slot (the WP-6 single commit) ---------------


def test_past_decisions_appear_in_the_next_recommendation_prompt(client, monkeypatch):
    """THE acceptance test: a recorded verdict is visible when the
    Recommender reasons about the next similar signal."""
    decide_via_api(
        client,
        verdict="reject",
        rationale="seasonal batch job, not actionable",
        service="compute",
        occurred_on="2026-07-01",
    )

    provider = SchemaAwareProvider({"RecommenderReport": HIGH_CONF_REPORT})
    monkeypatch.setattr(recommender, "get_provider", lambda: provider)
    fresh_event = seed_analyzed_event(service="compute", occurred_on="2026-07-05")
    client.post(f"/anomalies/{fresh_event}/recommend")

    prompt = provider.prompts[0]
    assert "Prior operator decisions on similar signals:" in prompt
    assert "operator rejected a proposal — seasonal batch job, not actionable" in prompt


def test_memory_of_other_services_stays_out_of_the_prompt(client, monkeypatch):
    decide_via_api(
        client, verdict="approve", rationale="storage-only context", service="storage", occurred_on="2026-07-01"
    )
    provider = SchemaAwareProvider({"RecommenderReport": HIGH_CONF_REPORT})
    monkeypatch.setattr(recommender, "get_provider", lambda: provider)
    fresh_event = seed_analyzed_event(service="compute", occurred_on="2026-07-05")
    client.post(f"/anomalies/{fresh_event}/recommend")

    prompt = provider.prompts[0]
    assert "Prior operator decisions" not in prompt
    assert "storage-only context" not in prompt


def seed_bare_action(detail_json: str = "{}") -> int:
    """An action with no event link — the service fallback's territory."""
    conn = db.connect()
    try:
        with db.writing(conn):
            cursor = conn.execute(
                "INSERT INTO actions (event_id, title, detail_json) "
                "VALUES (NULL, 'bare action', ?)",
                (detail_json,),
            )
            return cursor.lastrowid
    finally:
        conn.close()


def test_service_fallback_reads_the_detail_anomaly(client):
    action_id = seed_bare_action('{"anomaly": {"service": "fallback-svc"}}')
    assert client.post(f"/actions/{action_id}/approve").status_code == 200
    conn = db.connect()
    try:
        service = conn.execute("SELECT service FROM decisions").fetchone()[0]
    finally:
        conn.close()
    assert service == "fallback-svc"


@pytest.mark.parametrize(
    "detail",
    [
        # every product-reachable corruption is valid JSON (detail_json is
        # always written via json.dumps); non-JSON blobs stay out of scope
        "{}",
        '{"anomaly": null}',
        '{"anomaly": {"service": ["weird", "shape"]}}',
        '{"anomaly": {"service": ""}}',
    ],
)
def test_service_fallback_degrades_to_unknown_never_500(client, detail):
    """Any corrupt-but-JSON detail shape must degrade to 'unknown' — a 500
    here would brick the decide endpoint for that action forever."""
    action_id = seed_bare_action(detail)
    assert client.post(f"/actions/{action_id}/approve").status_code == 200
    conn = db.connect()
    try:
        service = conn.execute("SELECT service FROM decisions").fetchone()[0]
    finally:
        conn.close()
    assert service == "unknown"


def test_rationale_whitespace_normalizes_to_none(client):
    action_id = seed_bare_action()
    response = client.post(
        f"/actions/{action_id}/approve", json={"actor": "tuana", "rationale": "   "}
    )
    assert response.status_code == 200
    conn = db.connect()
    try:
        rationale = conn.execute("SELECT rationale FROM decisions").fetchone()[0]
    finally:
        conn.close()
    assert rationale is None


def test_rationale_over_limit_is_422(client):
    action_id = seed_bare_action()
    response = client.post(
        f"/actions/{action_id}/approve", json={"rationale": "x" * 501}
    )
    assert response.status_code == 422


def test_similar_limit_is_bounded_and_effective(client):
    for occurred_on in ("2026-07-01", "2026-07-02", "2026-07-03"):
        decide_via_api(
            client, verdict="approve", rationale=occurred_on, service="compute", occurred_on=occurred_on
        )
    limited = client.get(
        "/decisions/similar", params={"service": "compute", "limit": 1}
    ).json()
    assert limited["count"] == 1
    assert limited["decisions"][0]["rationale"] == "2026-07-03"  # newest
    assert client.get("/decisions/similar", params={"service": "compute", "limit": 0}).status_code == 422
    assert client.get("/decisions/similar", params={"service": "compute", "limit": 51}).status_code == 422


def test_fetch_decision_memory_matches_case_insensitively(client):
    decide_via_api(
        client, verdict="approve", rationale="cased", service="compute", occurred_on="2026-07-01"
    )
    conn = db.connect()
    try:
        digest = recommender.fetch_decision_memory(conn, "COMPUTE")
    finally:
        conn.close()
    assert "cased" in digest


def test_memory_digest_is_capped(client):
    conn = db.connect()
    try:
        with db.writing(conn):
            for index in range(8):
                conn.execute(
                    "INSERT INTO decisions (action_id, service, verdict, rationale, input_context_json) "
                    "VALUES (NULL, 'compute', 'approved', ?, '{}')",
                    (f"rationale-{index}",),
                )
    finally:
        conn.close()
    conn = db.connect()
    try:
        digest = recommender.fetch_decision_memory(conn, "compute")
    finally:
        conn.close()
    lines = digest.splitlines()
    assert len(lines) == recommender.DECISION_MEMORY_LIMIT
    assert "rationale-7" in lines[0]  # newest first
