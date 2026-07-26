"""Tests for the self-telemetry organ (live-data trial).

Acceptance criteria: every served request lands in the (organ, day)
counters, GET /telemetry/usage answers the cost-dataset contract with
real requests/day, SENTINEL_SELF_TELEMETRY=0 switches recording off,
and SENTINEL_COSTS_SOURCE=self routes the whole cost lane over the
recorded history — honestly (no fabricated days).
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import db, telemetry
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_organ_mapping_groups_paths_into_rooms():
    assert telemetry.organ_for_path("/costs/summary") == "watch"
    assert telemetry.organ_for_path("/anomalies") == "watch"
    assert telemetry.organ_for_path("/pulse") == "agents"
    assert telemetry.organ_for_path("/security/signals") == "security"
    assert telemetry.organ_for_path("/fraud/signals") == "fraud"
    assert telemetry.organ_for_path("/insights/self-review") == "brain"
    assert telemetry.organ_for_path("/auth/login") == "auth"
    assert telemetry.organ_for_path("/") == "dashboard"
    assert telemetry.organ_for_path("/static/app.js") == "dashboard"
    assert telemetry.organ_for_path("/telemetry/usage") == "dashboard"


def test_served_requests_land_in_the_usage_dataset(client):
    # computed before the requests: the recorded keys and the expected key
    # must come from the same side of any UTC midnight boundary
    today = datetime.now(timezone.utc).date().isoformat()
    client.get("/costs/summary")
    client.get("/costs/summary")
    client.get("/security/signals")
    body = client.get("/telemetry/usage").json()
    assert body["currency"] == "req"
    counts = {
        (r["service"], r["date"]): r["cost"] for r in body["daily_costs"]
    }
    assert counts[("watch", today)] >= 2.0
    assert counts[("security", today)] >= 1.0
    assert body["period"]["start"] <= today <= body["period"]["end"]


def test_disabled_telemetry_records_nothing(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_SELF_TELEMETRY", "0")
    client.get("/costs/summary")
    body = client.get("/telemetry/usage").json()
    assert body["daily_costs"] == []


def test_self_source_routes_the_cost_lane_over_real_usage(client, monkeypatch):
    client.get("/security/signals")  # one real request on record
    monkeypatch.setenv("SENTINEL_COSTS_SOURCE", "self")
    summary = client.get("/costs/summary").json()
    assert summary["currency"] == "req"
    services = {s["service"] for s in summary["services"]}
    assert "security" in services
    # the usage endpoint and the cost lane tell the same story
    usage = client.get("/telemetry/usage").json()
    assert {r["service"] for r in usage["daily_costs"]} >= services


def test_size_triggered_flush_reaches_the_database(monkeypatch):
    monkeypatch.setattr(telemetry, "FLUSH_EVERY", 3)
    assert telemetry.record("/costs/summary") is False
    assert telemetry.record("/costs/summary") is False
    # the threshold signals the caller (the middleware) to flush
    assert telemetry.record("/costs/summary") is True
    telemetry.flush()
    conn = db.connect_ready()
    try:
        rows = conn.execute(
            "SELECT service, hits FROM telemetry_usage"
        ).fetchall()
    finally:
        conn.close()
    assert [(row["service"], row["hits"]) for row in rows] == [("watch", 3)]


def test_failed_flush_rebuffers_and_cools_down(monkeypatch):
    monkeypatch.setattr(telemetry, "FLUSH_EVERY", 1)
    original_connect = db.connect_ready

    def broken_connect(*args, **kwargs):
        raise RuntimeError("database gone")

    assert telemetry.record("/costs/summary") is True
    monkeypatch.setattr(db, "connect_ready", broken_connect)
    telemetry.flush()  # must not raise — counts are rebuffered
    # during the cooldown the threshold no longer signals a flush,
    # so a broken DB cannot turn into a per-request retry storm
    assert telemetry.record("/costs/summary") is False
    monkeypatch.setattr(db, "connect_ready", original_connect)
    dataset = telemetry.usage_dataset()  # read path still flushes
    assert sum(r["cost"] for r in dataset["daily_costs"]) == 2.0


def test_empty_history_yields_a_well_formed_empty_dataset(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_SELF_TELEMETRY", "0")
    monkeypatch.setenv("SENTINEL_COSTS_SOURCE", "self")
    telemetry.reset_buffer()
    summary = client.get("/costs/summary").json()
    assert summary["records_analyzed"] == 0
    assert summary["total_cost"] == 0
    anomalies = client.get("/anomalies").json()
    assert anomalies["anomaly_count"] == 0
