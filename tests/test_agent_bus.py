"""Tests for the agent bus: the persisted inter-agent feed, its cursor
endpoint and the agent roster manifest."""

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_pulse_publishes_the_whole_conversation(client):
    client.post("/pulse").json()
    feed = client.get("/agents/feed").json()
    assert feed["count"] > 0
    agents = {event["agent"] for event in feed["events"]}
    # the full cast appears: opener, triage, drafting, review, narration
    assert {"reflex", "analyst", "recommender", "skeptic", "chronicler"} <= agents
    # ids ascend — the feed replays in order
    ids = [event["id"] for event in feed["events"]]
    assert ids == sorted(ids)
    # the dialogue is visible: a skeptic request precedes its verdict
    kinds = [event["kind"] for event in feed["events"]]
    assert kinds.index("escalate") < kinds.index("verdict")


def test_feed_cursor_returns_only_new_events(client):
    client.post("/pulse")
    first = client.get("/agents/feed").json()
    assert client.get("/agents/feed", params={"after": first["last_id"]}).json()["count"] == 0
    # an operator decision lands on the bus as a new event
    action_id = client.get("/actions").json()["actions"][0]["id"]
    client.post(f"/actions/{action_id}/reject", json={"actor": "op", "rationale": "test"})
    fresh = client.get("/agents/feed", params={"after": first["last_id"]}).json()
    assert fresh["count"] == 1
    assert fresh["events"][0]["agent"] == "operator"
    assert "REJECTED" in fresh["events"][0]["message"]


def test_roster_names_the_six_agents_with_guardrails(client):
    body = client.get("/agents").json()
    assert body["count"] == 6
    assert body["debate_threshold"] == 0.6
    names = [agent["name"] for agent in body["agents"]]
    assert names == ["reflex", "analyst", "recommender", "skeptic", "chronicler", "operator"]
    assert all(agent["guardrails"] for agent in body["agents"])
