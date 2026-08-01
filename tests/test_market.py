"""Tests for the market-watch lane (standing optimisation opportunities)."""

import json

import pytest
from fastapi.testclient import TestClient

from app import feeds, market
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_market_lane(monkeypatch):
    """Every test starts on the curated catalogue with an empty feed cache."""
    monkeypatch.delenv(market.MARKET_FEED_ENV, raising=False)
    feeds.reset_cache()
    yield
    feeds.reset_cache()


def test_opportunities_are_ranked_and_costed():
    report = client.get("/market/opportunities").json()

    assert report["source"] == "curated"
    assert report["opportunity_count"] > 0
    midpoints = [o["monthly_saving_mid"] for o in report["opportunities"]]
    assert midpoints == sorted(midpoints, reverse=True), "biggest credible win first"

    for opportunity in report["opportunities"]:
        assert opportunity["monthly_saving_low"] <= opportunity["monthly_saving_high"]
        assert opportunity["source"], "every row carries its provenance"
        assert opportunity["basis"], "every row shows the arithmetic it rests on"


def test_savings_are_arithmetic_over_the_estate_run_rate():
    """run rate × addressable share × published band — recomputed by hand."""
    report = client.get("/market/opportunities").json()
    opportunity = report["opportunities"][0]

    addressable = (
        opportunity["service_monthly_run_rate"] * opportunity["share_of_service_spend"]
    )
    low, high = (
        int(part.rstrip("%")) / 100
        for part in opportunity["reduction_band"].replace("–", "-").split("-")
    )
    assert opportunity["monthly_saving_low"] == pytest.approx(addressable * low, abs=0.01)
    assert opportunity["monthly_saving_high"] == pytest.approx(addressable * high, abs=0.01)


def test_only_services_the_estate_runs_are_matched():
    """A catalogue entry for a service outside the estate never appears."""
    estate = {s["service"].lower() for s in client.get("/costs/summary").json()["services"]}
    report = client.get("/market/opportunities").json()
    assert {o["service"] for o in report["opportunities"]} <= estate


def test_minimum_saving_filters_pocket_change():
    everything = client.get("/market/opportunities").json()
    floor = max(o["monthly_saving_high"] for o in everything["opportunities"])

    filtered = client.get(f"/market/opportunities?min_monthly_saving={floor}").json()
    assert filtered["opportunity_count"] == 1
    assert filtered["opportunity_count"] < everything["opportunity_count"]


def test_gross_total_is_flagged_as_overlapping_not_a_forecast():
    report = client.get("/market/opportunities").json()
    assert report["gross_monthly_low"] <= report["gross_monthly_high"]
    # The bundled catalogue puts several moves on compute; the report must
    # say so rather than presenting the sum as a promise.
    assert report["overlapping_services"]
    assert "upper bound" in report["note"]


def test_malformed_catalogue_entries_are_dropped():
    """Percent-instead-of-fraction and missing fields must not ship savings."""
    catalogue = market._validate_catalogue(
        {
            "reviewed": "2026-08-01",
            "opportunities": [
                {  # valid
                    "id": "OK-1",
                    "headline": "keeps",
                    "applies_to": ["compute"],
                    "applies_to_share": 0.5,
                    "reduction_min": 0.1,
                    "reduction_max": 0.2,
                },
                {  # 35 instead of 0.35 — would invent a 35× saving
                    "id": "BAD-1",
                    "headline": "percent not fraction",
                    "applies_to": ["compute"],
                    "applies_to_share": 35,
                    "reduction_min": 0.1,
                    "reduction_max": 0.2,
                },
                {  # inverted band
                    "id": "BAD-2",
                    "headline": "inverted",
                    "applies_to": ["compute"],
                    "applies_to_share": 0.5,
                    "reduction_min": 0.9,
                    "reduction_max": 0.1,
                },
                {  # no services
                    "id": "BAD-3",
                    "headline": "unattached",
                    "applies_to": [],
                    "applies_to_share": 0.5,
                    "reduction_min": 0.1,
                    "reduction_max": 0.2,
                },
            ],
        }
    )
    assert [entry["id"] for entry in catalogue["opportunities"]] == ["OK-1"]


def test_catalogue_with_no_valid_entry_is_rejected():
    with pytest.raises(ValueError):
        market._validate_catalogue({"opportunities": [{"id": "x"}]})


def test_feed_lane_serves_an_external_catalogue(monkeypatch):
    monkeypatch.setenv(market.MARKET_FEED_ENV, "https://feed.example/market.json")
    monkeypatch.setattr(
        feeds,
        "_get_json",
        lambda url: {
            "reviewed": "2026-08-01",
            "opportunities": [
                {
                    "id": "FEED-1",
                    "headline": "from the feed",
                    "category": "commitment",
                    "applies_to": ["compute"],
                    "applies_to_share": 0.5,
                    "reduction_min": 0.1,
                    "reduction_max": 0.2,
                    "source": "external catalogue",
                }
            ],
        },
    )

    report = client.get("/market/opportunities").json()
    assert report["source"] == "feed"
    assert [o["id"] for o in report["opportunities"]] == ["FEED-1"]


def test_dead_feed_falls_back_to_the_curated_catalogue(monkeypatch):
    """A configured feed that cannot answer must not claim to be live."""
    monkeypatch.setenv(market.MARKET_FEED_ENV, "https://feed.example/market.json")

    def boom(url):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(feeds, "_get_json", boom)

    report = client.get("/market/opportunities").json()
    assert report["source"] == feeds.MOCK_FALLBACK
    assert report["opportunity_count"] > 0, "the table still answers"


def test_bundled_catalogue_declares_provenance_on_every_entry():
    with market.MARKET_DATA_FILE.open() as f:
        catalogue = json.load(f)
    for entry in catalogue["opportunities"]:
        assert entry["source"] and entry["checked"]
        assert entry["watch_out"], "every move states what can go wrong"


def test_dashboard_ships_the_market_room():
    """The intel room carries the suggestions table with its provenance badge."""
    page = client.get("/").text
    assert 'id="sec-market"' in page
    assert 'id="market-source"' in page
    app_js = client.get("/static/app.js").text
    assert "renderMarket" in app_js
    assert "/market/opportunities" in app_js
    assert '"sec-market"' in app_js, "the room must be routed, not orphaned"


def test_market_lane_files_no_actions():
    """Suggestions never enter the decision inbox on their own."""
    before = client.get("/actions").json()["count"]
    client.get("/market/opportunities")
    assert client.get("/actions").json()["count"] == before
