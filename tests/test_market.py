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


def _opportunity(**overrides) -> market.MarketOpportunity:
    """A costed row with sane defaults; each test overrides only its subject."""
    row = {
        "id": "MW-TEST",
        "headline": "a move",
        "category": "waste",
        "service": "compute",
        "monthly_saving_low": 100.0,
        "monthly_saving_high": 150.0,
        "monthly_saving_mid": 125.0,
        "service_monthly_run_rate": 1000.0,
        "share_of_service_spend": 0.5,
        "reduction_band": "20–30%",
        "reduction_min": 0.2,
        "reduction_max": 0.3,
        "effort": "low",
        "risk": "low",
        "horizon": "days",
        "rationale": "why the move exists",
        "watch_out": "what can go wrong",
        "source": "a published pricing page",
        "checked": "2026-08-01",
        "basis": "1000.0/mo run rate × 50% addressable × 20–30% reduction",
    }
    row.update(overrides)
    return market.MarketOpportunity(**row)


SOLID = {"compute": market.ServiceFacts(share_of_tracked_spend=0.5, days_of_history=30)}


def test_suggestions_are_derived_from_signals_the_estate_actually_matches():
    report = client.get("/market/opportunities").json()
    shortlist = report["possible_suggestions"]
    costed = {o["id"] for o in report["opportunities"]}

    assert shortlist["suggestions"], "the mock estate must produce a shortlist"
    assert shortlist["shortlisted"] <= market.MAX_SUGGESTIONS, "short, not a table"
    assert shortlist["signals_matched"] <= shortlist["signals_available"]
    for suggestion in shortlist["suggestions"]:
        assert suggestion["signal"] in costed, "no suggestion without a costed signal"
        assert suggestion["source"] and suggestion["checked"], "provenance travels"
        assert suggestion["why_here"], "every line says why it applies to this estate"
        assert suggestion["confidence_basis"], "and what earned its label"


def test_every_suggestion_carries_an_explicit_confidence_label():
    shortlist = client.get("/market/opportunities").json()["possible_suggestions"]
    labels = {s["confidence"] for s in shortlist["suggestions"]}
    assert labels <= set(market.CONFIDENCE_ORDER), "no label outside the ladder"
    assert all(s["confidence"] for s in shortlist["suggestions"])


def test_suggestion_figures_are_the_table_s_own_arithmetic():
    """The shortlist may never disagree with the row it was derived from."""
    report = client.get("/market/opportunities").json()
    rows = {(o["id"], o["service"]): o for o in report["opportunities"]}
    for suggestion in report["possible_suggestions"]["suggestions"]:
        row = rows[(suggestion["signal"], suggestion["service"])]
        assert suggestion["monthly_saving_low"] == row["monthly_saving_low"]
        assert suggestion["monthly_saving_high"] == row["monthly_saving_high"]


def test_a_signal_is_suggested_once_on_its_most_valuable_service():
    """The same move on a second service is the same work, not a second line."""
    report = client.get("/market/opportunities").json()
    shortlist = report["possible_suggestions"]["suggestions"]
    assert len({s["signal"] for s in shortlist}) == len(shortlist)

    by_signal: dict[str, list[dict]] = {}
    for row in report["opportunities"]:
        by_signal.setdefault(row["id"], []).append(row)
    for suggestion in shortlist:
        rows = by_signal[suggestion["signal"]]
        best = max(r["monthly_saving_mid"] for r in rows)
        anchor = next(r for r in rows if r["service"] == suggestion["service"])
        assert anchor["monthly_saving_mid"] == best, "anchored to the best service"
        assert suggestion["also_applies_to"] == sorted(
            r["service"] for r in rows if r["service"] != suggestion["service"]
        )


def test_ranking_puts_trust_before_money():
    """A line still owed a human judgement cannot outrank one ready to start."""
    shortlist = market.derive_suggestions(
        [
            _opportunity(
                id="BIG-RISK",
                risk="high",
                monthly_saving_mid=9000.0,
                monthly_saving_high=9500.0,
            ),
            _opportunity(id="SMALL-SURE", monthly_saving_mid=12.0),
        ],
        SOLID,
        signals_available=2,
    )
    assert [s.signal for s in shortlist.suggestions] == ["SMALL-SURE", "BIG-RISK"]
    assert [s.rank for s in shortlist.suggestions] == [1, 2]
    assert shortlist.suggestions[1].confidence == market.NEEDS_REVIEW


def test_high_risk_and_missing_provenance_are_labelled_needs_review():
    labelled = {
        row.signal: (row.confidence, row.confidence_basis)
        for row in market.derive_suggestions(
            [
                _opportunity(id="RISKY", risk="high"),
                _opportunity(id="UNSOURCED", source="unattributed"),
                _opportunity(id="UNCHECKED", checked="unknown"),
                _opportunity(id="CLEAN"),
            ],
            SOLID,
            signals_available=4,
        ).suggestions
    }
    assert labelled["RISKY"][0] == market.NEEDS_REVIEW
    assert "high risk" in labelled["RISKY"][1]
    assert labelled["UNSOURCED"][0] == market.NEEDS_REVIEW
    assert labelled["UNCHECKED"][0] == market.NEEDS_REVIEW
    assert labelled["CLEAN"][0] == "high"


def test_thin_cost_history_never_earns_confidence():
    """A run rate over three days of data is not a run rate."""
    thin = {
        "compute": market.ServiceFacts(share_of_tracked_spend=0.5, days_of_history=3)
    }
    only = market.derive_suggestions([_opportunity()], thin, signals_available=1)
    suggestion = only.suggestions[0]
    assert suggestion.confidence == market.NEEDS_REVIEW
    assert "3 day(s)" in suggestion.confidence_basis
    assert str(market.MIN_HISTORY) in suggestion.confidence_basis


def test_a_wide_published_band_is_moderate_not_high():
    """A band whose high end doubles its low is a range, not an estimate."""
    by_signal = {
        row.signal: row
        for row in market.derive_suggestions(
            [
                _opportunity(id="WIDE", reduction_min=0.1, reduction_max=0.5),
                _opportunity(id="TIGHT", reduction_min=0.1, reduction_max=0.15),
            ],
            SOLID,
            signals_available=2,
        ).suggestions
    }
    wide, tight = by_signal["WIDE"], by_signal["TIGHT"]
    assert tight.confidence == "high"
    assert wide.confidence == "moderate"
    assert "double" in wide.confidence_basis


def test_medium_risk_or_effort_hedges_the_label():
    hedged = market.derive_suggestions(
        [_opportunity(risk="medium", effort="medium")], SOLID, signals_available=1
    ).suggestions[0]
    assert hedged.confidence == "moderate"
    assert "medium risk" in hedged.confidence_basis
    assert "medium effort" in hedged.confidence_basis


def test_an_unknown_service_falls_back_to_needs_review():
    """No cost record for the service means no confidence, not a flattering one."""
    orphan = market.derive_suggestions(
        [_opportunity(service="quantum")], SOLID, signals_available=1
    ).suggestions[0]
    assert orphan.confidence == market.NEEDS_REVIEW


def test_nothing_applicable_says_so_plainly():
    """An empty shortlist is a finding, and it must read like one."""
    empty = market.derive_suggestions([], {}, signals_available=9)
    assert empty.suggestions == []
    assert empty.signals_matched == 0
    assert empty.shortlisted == 0
    assert "9 bundled signal(s)" in empty.note
    assert "will not invent" in empty.note


def test_shortlist_is_capped_and_deterministic():
    rows = [
        _opportunity(id=f"MW-{i:02d}", monthly_saving_mid=float(i)) for i in range(9)
    ]
    first = market.derive_suggestions(rows, SOLID, signals_available=9, limit=3)
    second = market.derive_suggestions(
        list(reversed(rows)), SOLID, signals_available=9, limit=3
    )

    assert first.shortlisted == 3
    assert first.signals_matched == 9, "counts matches, not shortlisted lines"
    assert [s.signal for s in first.suggestions] == [
        s.signal for s in second.suggestions
    ]


def test_why_here_quotes_the_estate_not_the_catalogue():
    """The 'why this estate' sentence must carry the estate's own numbers."""
    suggestion = market.derive_suggestions(
        [_opportunity(service_monthly_run_rate=4200.0, share_of_service_spend=0.6)],
        SOLID,
        signals_available=1,
    ).suggestions[0]
    assert "4,200.00/mo" in suggestion.why_here
    assert "50% of this estate's tracked spend" in suggestion.why_here  # SOLID share
    assert "60% of that" in suggestion.why_here  # the catalogue's assumption
    assert "30 day(s) of cost history" in suggestion.why_here


def test_the_operator_floor_narrows_the_shortlist_too():
    """Raising the 'don't show me pocket change' knob must move both halves."""
    everything = client.get("/market/opportunities").json()
    floor = max(o["monthly_saving_high"] for o in everything["opportunities"])
    filtered = client.get(f"/market/opportunities?min_monthly_saving={floor}").json()

    assert filtered["possible_suggestions"]["shortlisted"] == 1
    assert (
        filtered["possible_suggestions"]["shortlisted"]
        < everything["possible_suggestions"]["shortlisted"]
    )


def test_suggestions_are_free_of_model_generated_text():
    """Every sentence on a suggestion traces to the catalogue or to arithmetic."""
    with market.MARKET_DATA_FILE.open() as f:
        catalogue = {entry["id"]: entry for entry in json.load(f)["opportunities"]}
    report = client.get("/market/opportunities").json()
    for suggestion in report["possible_suggestions"]["suggestions"]:
        entry = catalogue[suggestion["signal"]]
        assert suggestion["signal_headline"] == entry["headline"]
        assert suggestion["evidence"] == entry["rationale"]
        assert suggestion["watch_out"] == entry["watch_out"]
        assert suggestion["source"] == entry["source"]
        assert entry["headline"] in suggestion["suggestion"]


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


def test_run_rate_is_pinned_to_the_estate_cost_data():
    """The echoed run rate must be the estate's own mean daily cost × 30 —
    the organ's headline claim ('costed against this estate') frozen against
    the cost lane itself, not against the response's own echo."""
    mean_by_service = {
        row["service"].lower(): row["mean_daily_cost"]
        for row in client.get("/costs/summary").json()["services"]
    }
    report = client.get("/market/opportunities").json()
    assert report["opportunities"], "the mock estate must match opportunities"
    for opportunity in report["opportunities"]:
        service = opportunity["service"].lower()
        assert service in mean_by_service
        assert opportunity["service_monthly_run_rate"] == pytest.approx(
            mean_by_service[service] * 30, abs=0.51
        )
