"""Tests for the live-data feed layer (Sprint 3, live-data trial).

Acceptance criteria: every lane keeps its bundled mock as the default
(the suite stays hermetic), a configured feed URL flips the lane live
with the exact mock JSON contract, malformed feed records are dropped
instead of poisoning the detectors, failures fall back fetch -> last
good payload -> mock, and /health names each lane's source.
"""

import pytest
from fastapi.testclient import TestClient

from app import db, feeds
from app.detection import load_dataset
from app.fraud import load_fraud_dataset
from app.security import load_security_dataset
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


COSTS_FEED = {
    "currency": "EUR",
    "daily_costs": [
        {"date": "2026-07-20", "service": "edge", "cost": 10.0},
        {"date": "2026-07-21", "service": "edge", "cost": "12.5"},
        {"date": "not-a-date", "service": "edge", "cost": 1.0},
        {"date": "2026-07-22", "service": "", "cost": 1.0},
        {"date": "2026-07-22", "service": "edge", "cost": None},
    ],
}

SECURITY_FEED = {
    "metric": "failed_login_count",
    "daily_counts": [
        {"service": "gateway", "date": "2026-07-20", "count": 7},
        {"service": "gateway", "date": "2026-07-21", "count": "oops"},
    ],
}

FRAUD_FEED = {
    "currency": "USD",
    "events": [
        {
            "id": "TX-9001",
            "date": "2026-07-21",
            "service": "payments",
            "amount": 500.0,
            "typical_amount": 100.0,
            "account_age_days": 5,
            "country": "RO",
            "home_country": "TR",
            "tx_last_10m": 6,
        },
        # missing enrichment fields — must be dropped, not quietly mis-scored
        {"id": "TX-9002", "date": "2026-07-21", "amount": 50.0},
        # present but non-numeric enrichment — float()/int() in scoring
        # would 500 every scan; the validator must drop it instead
        {
            "id": "TX-9003",
            "date": "2026-07-21",
            "amount": 75.0,
            "typical_amount": "n/a",
            "account_age_days": 30,
            "country": "TR",
            "home_country": "TR",
            "tx_last_10m": 1,
        },
    ],
}


def test_sources_default_to_mock():
    assert feeds.data_sources() == {
        "costs": "mock",
        "security": "mock",
        "fraud": "mock",
    }


def test_costs_source_resolution(monkeypatch):
    monkeypatch.setenv("SENTINEL_COSTS_SOURCE", "self")
    assert feeds.costs_source() == "self"
    monkeypatch.setenv("SENTINEL_COSTS_SOURCE", "garbage")
    assert feeds.costs_source() == "mock"
    monkeypatch.delenv("SENTINEL_COSTS_SOURCE")
    monkeypatch.setenv("SENTINEL_COSTS_FEED_URL", "https://example.test/costs")
    assert feeds.costs_source() == "feed"
    # explicit mock wins over a configured feed URL
    monkeypatch.setenv("SENTINEL_COSTS_SOURCE", "mock")
    assert feeds.costs_source() == "mock"


def test_costs_feed_goes_live_and_drops_malformed_records(monkeypatch):
    monkeypatch.setenv("SENTINEL_COSTS_FEED_URL", "https://example.test/costs")
    monkeypatch.setattr(feeds, "_get_json", lambda url: COSTS_FEED)
    dataset = load_dataset()
    assert dataset["currency"] == "EUR"
    assert dataset["daily_costs"] == [
        {"date": "2026-07-20", "service": "edge", "cost": 10.0},
        {"date": "2026-07-21", "service": "edge", "cost": 12.5},
    ]
    # period is derived when the feed omits it
    assert dataset["period"] == {"start": "2026-07-20", "end": "2026-07-21"}


def test_feed_payload_is_cached_within_ttl(monkeypatch):
    calls = []

    def fake_get(url):
        calls.append(url)
        return COSTS_FEED

    monkeypatch.setenv("SENTINEL_COSTS_FEED_URL", "https://example.test/costs")
    monkeypatch.setattr(feeds, "_get_json", fake_get)
    load_dataset()
    load_dataset()
    assert len(calls) == 1
    # TTL 0 opts out of caching: every read refetches
    monkeypatch.setenv("SENTINEL_FEED_TTL_SECONDS", "0")
    load_dataset()
    load_dataset()
    assert len(calls) == 3


def test_feed_failure_serves_last_good_payload_then_mock(monkeypatch):
    monkeypatch.setenv("SENTINEL_COSTS_FEED_URL", "https://example.test/costs")
    monkeypatch.setenv("SENTINEL_FEED_TTL_SECONDS", "0")
    monkeypatch.setattr(feeds, "_get_json", lambda url: COSTS_FEED)
    live = load_dataset()
    assert live["currency"] == "EUR"

    def boom(url):
        raise RuntimeError("feed down")

    # stale-but-good beats dark panels
    monkeypatch.setattr(feeds, "_get_json", boom)
    assert load_dataset()["currency"] == "EUR"

    # no cached payload at all -> the bundled mock answers
    feeds.reset_cache()
    fallback = load_dataset()
    assert fallback["currency"] == "USD"
    assert len(fallback["daily_costs"]) == 56


def test_invalid_feed_payload_falls_back_to_mock(monkeypatch):
    monkeypatch.setenv("SENTINEL_COSTS_FEED_URL", "https://example.test/costs")
    monkeypatch.setattr(feeds, "_get_json", lambda url: {"nonsense": True})
    dataset = load_dataset()
    assert len(dataset["daily_costs"]) == 56


def test_costs_feed_sums_duplicates_and_drops_non_finite(monkeypatch):
    feed = {
        "daily_costs": [
            {"date": "2026-07-20", "service": "edge", "cost": 10.0},
            {"date": "2026-07-20", "service": "edge", "cost": 2.5},
            {"date": "2026-07-20", "service": "edge", "cost": float("nan")},
            {"date": "2026-07-20", "service": "edge", "cost": float("inf")},
        ]
    }
    monkeypatch.setenv("SENTINEL_COSTS_FEED_URL", "https://example.test/costs")
    monkeypatch.setattr(feeds, "_get_json", lambda url: feed)
    dataset = load_dataset()
    # one record per (service, date): duplicates summed, NaN/Inf dropped
    assert dataset["daily_costs"] == [
        {"date": "2026-07-20", "service": "edge", "cost": 12.5}
    ]


def test_failed_refresh_is_retried_once_per_ttl(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(feeds.time, "monotonic", lambda: clock["now"])
    calls = []

    def fake_get(url):
        calls.append(clock["now"])
        if len(calls) > 1:
            raise RuntimeError("feed down")
        return COSTS_FEED

    monkeypatch.setenv("SENTINEL_COSTS_FEED_URL", "https://example.test/costs")
    monkeypatch.setattr(feeds, "_get_json", fake_get)
    assert load_dataset()["currency"] == "EUR"  # t=1000: fresh fetch
    clock["now"] = 1400.0  # TTL (300) expired -> refresh fails, stale served
    assert load_dataset()["currency"] == "EUR"
    clock["now"] = 1500.0  # within the re-stamped TTL: no new attempt
    assert load_dataset()["currency"] == "EUR"
    assert calls == [1000.0, 1400.0]


def test_data_sources_report_the_served_source_not_the_config(monkeypatch):
    monkeypatch.setenv("SENTINEL_COSTS_FEED_URL", "https://example.test/costs")
    monkeypatch.setenv("SENTINEL_FEED_TTL_SECONDS", "0")

    def boom(url):
        raise RuntimeError("feed down")

    # configured live but never answered -> the truth is the mock fallback
    monkeypatch.setattr(feeds, "_get_json", boom)
    assert load_dataset()["currency"] == "USD"
    assert feeds.data_sources()["costs"] == "mock (feed unavailable)"
    # the feed recovers -> the lane reports live again
    monkeypatch.setattr(feeds, "_get_json", lambda url: COSTS_FEED)
    assert load_dataset()["currency"] == "EUR"
    assert feeds.data_sources()["costs"] == "feed"


def test_security_feed_goes_live_with_mock_contract(monkeypatch):
    monkeypatch.setenv("SENTINEL_SECURITY_FEED_URL", "https://example.test/sec")
    monkeypatch.setattr(feeds, "_get_json", lambda url: SECURITY_FEED)
    dataset = load_security_dataset()
    assert dataset["metric"] == "failed_login_count"
    assert dataset["daily_counts"] == [
        {"service": "gateway", "date": "2026-07-20", "count": 7}
    ]


def test_fraud_feed_requires_enrichment_fields(monkeypatch):
    monkeypatch.setenv("SENTINEL_FRAUD_FEED_URL", "https://example.test/fraud")
    monkeypatch.setattr(feeds, "_get_json", lambda url: FRAUD_FEED)
    dataset = load_fraud_dataset()
    assert [e["id"] for e in dataset["events"]] == ["TX-9001"]


def test_health_names_the_data_sources(client, monkeypatch):
    body = client.get("/health").json()
    assert body["data_sources"] == {
        "costs": "mock",
        "security": "mock",
        "fraud": "mock",
    }
    monkeypatch.setenv("SENTINEL_COSTS_SOURCE", "self")
    monkeypatch.setenv("SENTINEL_FRAUD_FEED_URL", "https://example.test/fraud")
    body = client.get("/health").json()
    assert body["data_sources"] == {
        "costs": "self",
        "security": "mock",
        "fraud": "feed",
    }


def test_upsert_event_refreshes_analysis_only_on_payload_change():
    conn = db.connect_ready()
    try:
        with db.writing(conn):
            event_id = db.upsert_event(
                conn,
                kind="cost_anomaly",
                service="edge",
                occurred_on="2026-07-20",
                payload_json='{"cost": 10.0}',
                refresh_analysis_on_change=True,
            )
            conn.execute(
                "UPDATE events SET analysis_json = ? WHERE id = ?",
                ('{"report": "pinned"}', event_id),
            )
        # identical payload: the analysis stays attached (mock lane, re-polls)
        with db.writing(conn):
            same = db.upsert_event(
                conn,
                kind="cost_anomaly",
                service="edge",
                occurred_on="2026-07-20",
                payload_json='{"cost": 10.0}',
                refresh_analysis_on_change=True,
            )
        row = conn.execute(
            "SELECT analysis_json FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        assert same == event_id
        assert row["analysis_json"] == '{"report": "pinned"}'
        # re-stated figures: the stale analysis is dropped for re-triage
        with db.writing(conn):
            db.upsert_event(
                conn,
                kind="cost_anomaly",
                service="edge",
                occurred_on="2026-07-20",
                payload_json='{"cost": 99.0}',
                refresh_analysis_on_change=True,
            )
        row = conn.execute(
            "SELECT payload_json, analysis_json FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
        assert row["payload_json"] == '{"cost": 99.0}'
        assert row["analysis_json"] is None
    finally:
        conn.close()
