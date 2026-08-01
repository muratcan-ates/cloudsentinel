"""Opt-in webhook dispatch on execute (``SENTINEL_EXECUTE_WEBHOOK_URL``).

Acceptance criteria:
- knob unset → execute behaves exactly as before: no network attempt, no
  ``execution.dispatch`` key in the audit detail;
- knob set + delivery succeeds → ``execution.dispatch`` records
  delivered=True, echoes the endpoint's status, and stores the target HOST
  ONLY — the full URL (it may embed a secret token) appears nowhere in
  detail_json or the log stream — and one bus line announces the dispatch;
- knob set + delivery raises → the execute still answers 200/executed (the
  state machine never depends on a webhook's mood) and the honest
  failed-note is the audit record; a non-2xx answer is a failure too;
- an Idempotency-Key replay never re-fires the webhook;
- the payload carries the incident contract: action id, title, service,
  stance, risk, a numeric estimated saving, decider, timestamp and the
  Markdown incident report.
"""

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db
from main import app

WEBHOOK = "https://hooks.example.test/services/T000/B000/tok-secret-abc123"


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def seed_approved_action() -> int:
    """One approved action carrying the evidence pack the payload reads."""
    detail = {
        "preferred": "CAUTIOUS",
        "options": [
            {
                "stance": "CAUTIOUS",
                "title": "scale down idle instances",
                "risk": "low",
                "estimated_monthly_saving": 120.5,
                "rollback": "scale back up",
            },
            {
                "stance": "BOLD",
                "title": "terminate the fleet",
                "risk": "medium",
                "estimated_monthly_saving": 300.0,
                "rollback": "relaunch from the template",
            },
        ],
        "savings": {
            "daily_excess": 4.0,
            "cautious_monthly": 120.5,
            "bold_monthly": 300.0,
        },
        "anomaly": {
            "service": "compute",
            "date": "2026-07-12",
            "cost": 9.9,
            "service_mean": 5.1,
            "z_score": 3.5,
            "severity": "critical",
            "detector": "zscore",
        },
    }
    conn = db.connect()
    try:
        with db.writing(conn):
            event_id = db.upsert_event(
                conn,
                kind="cost_anomaly",
                service="compute",
                occurred_on="2026-07-12",
                payload_json="{}",
            )
            cursor = conn.execute(
                "INSERT INTO actions "
                "(event_id, title, detail_json, state, decided_at, decided_by) "
                "VALUES (?, 'scale down idle instances', ?, 'approved', "
                "datetime('now'), 'erin')",
                (event_id, json.dumps(detail)),
            )
            return cursor.lastrowid
    finally:
        conn.close()


def stored_detail(action_id: int) -> dict:
    """The raw persisted detail_json — what is REALLY in the audit trail."""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT detail_json FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row["detail_json"])


class _Response:
    def __init__(self, status_code: int = 204):
        self.status_code = status_code


def test_execute_without_webhook_is_unchanged(client, monkeypatch):
    def _forbidden(*args, **kwargs):
        raise AssertionError("no webhook configured — nothing may leave")

    monkeypatch.setattr(httpx, "post", _forbidden)
    action_id = seed_approved_action()
    response = client.post(f"/actions/{action_id}/execute")
    assert response.status_code == 200
    assert response.json()["detail"]["execution"]["mode"] == "SIMULATION"
    assert "dispatch" not in stored_detail(action_id)["execution"]


def test_dispatch_success_lands_in_the_audit_detail(client, monkeypatch, caplog):
    monkeypatch.setenv("SENTINEL_EXECUTE_WEBHOOK_URL", WEBHOOK)
    sent = {}

    def _fake_post(url, json=None, timeout=None):
        sent["url"], sent["payload"], sent["timeout"] = url, json, timeout
        return _Response(204)

    monkeypatch.setattr(httpx, "post", _fake_post)
    action_id = seed_approved_action()
    with caplog.at_level(logging.INFO, logger="cloudsentinel"):
        response = client.post(f"/actions/{action_id}/execute")
    assert response.status_code == 200
    assert response.json()["state"] == "executed"
    # infrastructure mutation stays simulated — the stamp is untouched
    execution = stored_detail(action_id)["execution"]
    assert execution["mode"] == "SIMULATION"
    outcome = execution["dispatch"]
    assert outcome["delivered"] is True
    assert outcome["status"] == 204
    assert outcome["target"] == "hooks.example.test"  # host only, never the URL
    assert outcome["note"] == "delivered"
    # the real POST used the full URL with a sane timeout...
    assert sent["url"] == WEBHOOK
    assert sent["timeout"] == pytest.approx(10.0)
    # ...but the secret-bearing URL is nowhere in storage or the log stream
    raw = json.dumps(stored_detail(action_id))
    assert "tok-secret-abc123" not in raw
    assert WEBHOOK not in raw
    assert "tok-secret-abc123" not in caplog.text
    assert any("dispatched" in record.getMessage() for record in caplog.records)
    # and one bus line announces the dispatch to the live feed
    feed = client.get("/agents/feed").json()
    assert any(
        event["kind"] == "dispatch" and "hooks.example.test" in event["message"]
        for event in feed["events"]
    )


def test_dispatch_failure_never_fails_the_execute(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_EXECUTE_WEBHOOK_URL", WEBHOOK)

    def _down(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _down)
    action_id = seed_approved_action()
    response = client.post(f"/actions/{action_id}/execute")
    assert response.status_code == 200
    assert response.json()["state"] == "executed"
    outcome = stored_detail(action_id)["execution"]["dispatch"]
    assert outcome["delivered"] is False
    assert outcome["status"] is None
    assert outcome["target"] == "hooks.example.test"
    assert outcome["note"].startswith("failed:")


def test_non_2xx_answer_is_an_honest_failure(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_EXECUTE_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Response(500))
    action_id = seed_approved_action()
    assert client.post(f"/actions/{action_id}/execute").status_code == 200
    outcome = stored_detail(action_id)["execution"]["dispatch"]
    assert outcome["delivered"] is False
    assert outcome["status"] == 500
    assert "500" in outcome["note"]


def test_idempotent_replay_does_not_redispatch(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_EXECUTE_WEBHOOK_URL", WEBHOOK)
    calls = []

    def _count(*args, **kwargs):
        calls.append(1)
        return _Response(200)

    monkeypatch.setattr(httpx, "post", _count)
    action_id = seed_approved_action()
    headers = {"Idempotency-Key": "ship-once"}
    first = client.post(f"/actions/{action_id}/execute", headers=headers)
    second = client.post(f"/actions/{action_id}/execute", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(calls) == 1  # the replayed response never re-fires the webhook


def test_dispatch_payload_contract(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_EXECUTE_WEBHOOK_URL", WEBHOOK)
    sent = {}

    def _fake_post(url, json=None, timeout=None):
        sent["payload"] = json
        return _Response(200)

    monkeypatch.setattr(httpx, "post", _fake_post)
    action_id = seed_approved_action()
    client.post(f"/actions/{action_id}/execute")
    payload = sent["payload"]
    assert payload["action_id"] == action_id
    assert payload["title"] == "scale down idle instances"
    assert payload["service"] == "compute"
    assert payload["stance"] == "CAUTIOUS"
    assert payload["risk"] == "low"
    assert isinstance(payload["estimated_monthly_saving"], (int, float))
    assert payload["decided_by"] == "erin"
    assert payload["executed_at"]
    # the shipped report is the same document GET /actions/{id}/report serves
    assert "CloudSentinel Incident Report" in payload["report"]
    assert "## Computed savings" in payload["report"]
