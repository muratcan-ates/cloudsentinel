"""The simulated live tape: env-gated, honest, bounded, read-only."""

import pytest
from fastapi.testclient import TestClient

from app import stream
from main import app

client = TestClient(app)


@pytest.fixture()
def tape(monkeypatch):
    monkeypatch.setenv("SENTINEL_SIM_STREAM", "1")
    yield client


def test_stream_is_dark_without_the_flag():
    """No flag, no tape — a deployment without it never hints at a ticker."""
    response = client.get("/stream/metrics")
    assert response.status_code == 404
    assert "not enabled" in response.json()["detail"]


def test_stream_frame_shape_and_honest_labels(tape):
    body = tape.get("/stream/metrics").json()
    assert body["simulated"] is True
    assert "synthetic" in body["note"]
    assert "synthetic" in body["unit"]
    assert body["interval_seconds"] == stream.TICK_SECONDS
    assert body["services"], "the tape must carry the estate's services"
    for lane in body["services"]:
        assert lane["rate"] > 0
        assert 2 <= len(lane["trend"]) <= stream.TREND_LENGTH
        assert isinstance(lane["spiking"], bool)


def test_stream_mirrors_the_estate_services(tape):
    from app.detection import load_daily_costs

    estate = {record["service"] for record in load_daily_costs()}
    tape_services = {
        lane["service"] for lane in tape.get("/stream/metrics").json()["services"]
    }
    assert tape_services == estate


def test_stream_walk_advances_and_stays_bounded(tape, monkeypatch):
    """Ticks move the rates but the band keeps them from running away."""
    first = {
        lane["service"]: lane["rate"]
        for lane in tape.get("/stream/metrics").json()["services"]
    }
    monkeypatch.setattr(stream, "TICK_SECONDS", 0.000001)
    second = tape.get("/stream/metrics").json()["services"]
    moved = [lane for lane in second if lane["rate"] != first[lane["service"]]]
    assert moved, "a full catch-up burst must move at least one lane"
    for lane in second:
        base = stream._lanes[lane["service"]]["base"]
        assert base * stream.BAND[0] - 0.01 <= lane["rate"]
        assert lane["rate"] <= base * stream.BAND[1] + 0.01


def test_stream_survives_the_readonly_vitrine(tape, monkeypatch):
    """A read-only deployment may still run the tape — GET is never a write."""
    monkeypatch.setenv("SENTINEL_READONLY", "1")
    assert tape.get("/stream/metrics").status_code == 200


def test_sim_source_feeds_the_cost_lane(monkeypatch):
    """SENTINEL_COSTS_SOURCE=sim serves the generator's dataset and /health
    names the lane honestly — sim, never "live"."""
    monkeypatch.setenv("SENTINEL_COSTS_SOURCE", "sim")
    from app import detection

    dataset = detection.load_dataset()
    assert "synthetic" in dataset["description"]
    assert dataset["currency"] == "USD"
    days = {record["date"] for record in dataset["daily_costs"]}
    assert len(days) == stream.HISTORY_DAYS + 1
    assert client.get("/health").json()["data_sources"]["costs"] == "sim"


def test_sim_history_is_stable_but_today_moves(monkeypatch):
    """Hash-seeded history never rewrites itself; only today's projection
    rides the walk."""
    monkeypatch.setenv("SENTINEL_COSTS_SOURCE", "sim")
    from datetime import date

    from app import detection

    first = detection.load_dataset()["daily_costs"]
    monkeypatch.setattr(stream, "TICK_SECONDS", 0.000001)
    second = detection.load_dataset()["daily_costs"]
    today = date.today().isoformat()
    assert [r for r in first if r["date"] != today] == [
        r for r in second if r["date"] != today
    ]
    assert {r["service"]: r["cost"] for r in first if r["date"] == today} != {
        r["service"]: r["cost"] for r in second if r["date"] == today
    }


def test_sim_spike_becomes_a_real_signal(monkeypatch):
    """A stream spike projects into today's figure and the detector
    genuinely flags it — the whole chain reacts to the simulation."""
    monkeypatch.setenv("SENTINEL_COSTS_SOURCE", "sim")
    from datetime import date

    stream._seed()
    lane = stream._lanes["compute"]
    lane["rate"] = lane["base"] * 2.5
    body = client.get("/anomalies").json()
    flagged = {(a["service"], a["date"]) for a in body["anomalies"]}
    assert ("compute", date.today().isoformat()) in flagged
