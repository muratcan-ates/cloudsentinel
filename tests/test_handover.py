"""The shift-handover brief answers the standing operator questions from
persisted state, and the dashboard exposes a printable trigger."""

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_handover_summarizes_open_pending_and_decided(client):
    client.post("/pulse")  # fills signals + pending cards
    body = client.get("/analytics/handover").json()
    assert body["open_signals"] == 2
    assert body["critical_signals"] == 2
    assert body["pending_actions"] >= 2  # cost proposals (+ fraud holds)
    assert body["oldest_pending_hours"] is not None
    assert "projection" in body["forecast_note"]
    assert isinstance(body["pending"], list) and body["pending"]
    assert {"action_id", "service", "title", "age_hours"} <= set(body["pending"][0])


def test_handover_reflects_a_decision(client):
    report = client.post("/pulse").json()
    action_id = report["chain"][0]["action_id"]
    client.post(
        f"/actions/{action_id}/reject",
        json={"actor": "night-shift", "rationale": "planned migration window"},
    )
    body = client.get("/analytics/handover").json()
    assert body["recent_decisions"]
    top = body["recent_decisions"][0]
    assert top["verdict"] == "rejected"
    assert top["rationale"] == "planned migration window"


def test_dashboard_ships_the_handover_control():
    c = TestClient(app)
    page = c.get("/").text
    assert 'id="handover-print-btn"' in page
    assert 'id="handover-print"' in page
    app_js = c.get("/static/app.js").text
    assert "printHandover" in app_js
    assert "/analytics/handover" in app_js
