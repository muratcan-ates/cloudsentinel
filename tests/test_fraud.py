"""Tests for the fraud mission (Sprint 3, S3-② — deliberately minimal).

Acceptance criteria: the score is the published deterministic rule set
(reproducible by hand), bands are advisory only, and the wire copy says
so — nothing suggests automatic blocking.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import db
from app.fraud import band_for, score_breakdown, simple_score
from app.missions import FraudRules
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_hand_computed_scores_match(client):
    """TX-1004 by hand: amount 15.5x (>=10 -> 40) + velocity 6 (>=5 -> 25)
    + foreign country (20) + 12-day account (15) = 100."""
    body = client.get("/fraud/signals").json()
    by_id = {s["id"]: s for s in body["signals"]}
    assert by_id["TX-1004"]["score"] == 100
    assert by_id["TX-1004"]["band"] == "hold_suggested"
    assert by_id["TX-1010"]["score"] == 80  # 40 + 25 + 15, home country
    assert by_id["TX-1010"]["band"] == "hold_suggested"
    assert by_id["TX-1006"]["score"] == 60  # 25 + 15 + 20
    assert by_id["TX-1006"]["band"] == "review"
    assert by_id["TX-1001"]["score"] == 0
    assert by_id["TX-1001"]["band"] == "clear"
    assert by_id["TX-1001"]["reasons"] == []


def test_signals_sorted_by_score_desc_and_count_is_non_clear(client):
    body = client.get("/fraud/signals").json()
    scores = [s["score"] for s in body["signals"]]
    assert scores == sorted(scores, reverse=True)
    assert body["count"] == sum(1 for s in body["signals"] if s["band"] != "clear")
    assert body["count"] == 3  # TX-1004, TX-1010, TX-1006
    assert body["mission"] == "fraud"


def test_wire_copy_is_advisory_only(client):
    body = client.get("/fraud/signals").json()
    assert "operator" in body["note"]
    assert "human-in-the-loop" in body["note"]
    assert "automatically" in body["note"]  # "...nothing is blocked automatically"


def test_band_boundaries():
    assert band_for(70) == "hold_suggested"
    assert band_for(69) == "review"
    assert band_for(40) == "review"
    assert band_for(39) == "clear"
    assert band_for(0) == "clear"


def test_score_clamps_at_100():
    event = {
        "amount": 100000.0,
        "typical_amount": 10.0,
        "account_age_days": 1,
        "country": "XX",
        "home_country": "TR",
        "tx_last_10m": 99,
    }
    score, reasons = simple_score(event)
    assert score == 100
    assert len(reasons) == 4


def test_flagged_reasons_are_concrete():
    """Every scored signal explains itself in plain arithmetic terms."""
    event = {
        "amount": 960.0,
        "typical_amount": 280.0,
        "account_age_days": 460,
        "country": "TR",
        "home_country": "TR",
        "tx_last_10m": 2,
    }
    score, reasons = simple_score(event)
    assert score == 25
    assert reasons == ["amount 3.4x the account's typical"]


def test_rule_hits_attribute_every_point(client):
    """The structured breakdown accounts for the score line by line."""
    body = client.get("/fraud/signals").json()
    by_id = {s["id"]: s for s in body["signals"]}
    hits = by_id["TX-1004"]["rule_hits"]
    assert {h["rule"] for h in hits} == {"amount", "velocity", "geography", "account_age"}
    assert sum(h["points"] for h in hits) == 100
    for hit in hits:
        assert hit["detail"]  # every point names its arithmetic
    # the plain-text reasons stay derived from the same hits
    assert by_id["TX-1004"]["reasons"] == [h["detail"] for h in hits]


def test_score_breakdown_clamps_but_keeps_all_hits():
    event = {
        "amount": 100000.0,
        "typical_amount": 10.0,
        "account_age_days": 1,
        "country": "XX",
        "home_country": "TR",
        "tx_last_10m": 99,
    }
    score, hits = score_breakdown(event)
    assert score == 100
    assert sum(h.points for h in hits) == 100


def test_band_and_min_score_filters(client):
    full = client.get("/fraud/signals").json()
    review_only = client.get("/fraud/signals", params={"band": "review"}).json()
    assert {s["band"] for s in review_only["signals"]} == {"review"}
    high = client.get("/fraud/signals", params={"min_score": 70}).json()
    assert all(s["score"] >= 70 for s in high["signals"])
    # count and bands describe ALL scored events — filter-stable
    assert review_only["count"] == full["count"] == 3
    assert review_only["bands"] == full["bands"]
    assert full["bands"]["hold_suggested"] == 2
    assert full["bands"]["review"] == 1
    assert full["bands"]["clear"] == len(full["signals"]) - 3


def test_band_thresholds_come_from_the_mission(client):
    """configs/fraud.yaml rules are LIVE (unlike its inert detection block)."""
    from app.fraud import resolve_rules
    from app.missions import get_mission

    assert resolve_rules() == (70, 40, 30)
    assert get_mission("fraud").rules.hold_band == 70

    with pytest.raises(ValidationError, match="must sit below"):
        FraudRules(hold_band=40, review_band=70, new_account_days=30)


def test_flagged_signals_persist_without_polluting_the_funnel(client):
    before = client.get("/analytics/decisions").json()["funnel"]["signals"]
    body = client.get("/fraud/signals").json()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT service, occurred_on FROM events WHERE kind = 'fraud_signal'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == body["count"]  # every non-clear signal, exactly once
    assert {row["service"] for row in rows} == {"TX-1004", "TX-1010", "TX-1006"}
    # re-scan upserts by natural key: no duplicates
    client.get("/fraud/signals")
    conn = db.connect()
    try:
        again = conn.execute(
            "SELECT count(*) FROM events WHERE kind = 'fraud_signal'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert again == body["count"]
    # the HITL funnel counts cost anomalies only — fraud must not inflate it
    after = client.get("/analytics/decisions").json()["funnel"]["signals"]
    assert after == before


def test_pulse_sweeps_the_fraud_lane(client):
    report = client.post("/pulse").json()
    assert report["fraud_signals"] == 3
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT count(*) FROM events WHERE kind = 'fraud_signal'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert rows == 3
