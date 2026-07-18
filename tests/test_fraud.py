"""Tests for the fraud mission (Sprint 3, S3-② — deliberately minimal).

Acceptance criteria: the score is the published deterministic rule set
(reproducible by hand), bands are advisory only, and the wire copy says
so — nothing suggests automatic blocking.
"""

import pytest
from fastapi.testclient import TestClient

from app.fraud import band_for, simple_score
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
