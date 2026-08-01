"""The day-of-week baseline, finally exercised.

``app/detection.py`` has carried a seasonal path since Sprint 3 — Mondays
compared with Mondays — and nothing has ever run it. Not because it was
wrong, but because the bundled fixture is fourteen days long and the guard
that protects the path (a weekday bucket needs ``MIN_WEEKDAY_SAMPLES``
records AND ``n - 1 > threshold**2``) can never be satisfied by two weeks of
data at any window size. Shipped code, permanently unreachable in testing.

``app/data/seasonal_costs.json`` is ten weeks built for it, and the thing it
proves is a contrast rather than a number:

    2026-06-20 on analytics-batch costs 199.40 — a full weekday's worth of
    batch. That is an ordinary Tuesday and an impossible Saturday. The flat
    baseline pools both into one bimodal group and sees nothing; the weekday
    baseline compares Saturdays with Saturdays and flags it at once.

Same records, same detector, same threshold — only the seasonal switch
moves. The control service is in there for the opposite reason: a flat
estate must not acquire a weekly rhythm just because the option is on.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.detection import run_detection
from main import app

FIXTURE = Path(__file__).resolve().parent.parent / "app" / "data" / "seasonal_costs.json"

# The window has to span the ten weeks; the default 28 days would give each
# weekday bucket four samples and the guard would (correctly) refuse them.
WINDOW = 70
PLANTED = ("analytics-batch", "2026-06-20")
CONTROL = "storage-archive"


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def records():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["daily_costs"]


def _scan(records, seasonal: bool, threshold: float = 2.0):
    return run_detection(records, threshold=threshold, window=WINDOW, seasonal=seasonal)


# --- the fixture itself ------------------------------------------------------


def test_the_fixture_still_matches_its_generator():
    """A fixture nobody can regenerate is a fixture nobody can trust."""
    from scripts.make_seasonal_fixture import build

    assert json.dumps(build(), indent=2) + "\n" == FIXTURE.read_text(encoding="utf-8")


def test_the_fixture_is_ten_whole_weeks(records):
    dates = sorted({row["date"] for row in records})
    assert len(dates) == 70, "ten whole weeks, so every weekday bucket holds ten"
    services = {row["service"] for row in records}
    assert len(records) == len(dates) * len(services), "no service has a gap"


# --- the contrast the fixture exists for -------------------------------------


def test_the_flat_baseline_misses_the_planted_saturday(records):
    """Pooled weekdays and weekends make a spread the spike hides inside."""
    assert _scan(records, seasonal=False).anomalies == []


def test_the_weekday_baseline_catches_it(records):
    run = _scan(records, seasonal=True)
    assert [(a.service, a.date) for a in run.anomalies] == [PLANTED]
    flagged = run.anomalies[0]
    assert flagged.detector == "zscore+weekday", "the seasonal path must be the one that ran"
    assert flagged.z_score >= 2.0
    assert flagged.detector_params["seasonal"] is True


def test_the_planted_value_really_is_an_ordinary_weekday(records):
    """Otherwise the contrast is a trick: it has to be innocent on a Tuesday."""
    planted_cost = next(
        row["cost"]
        for row in records
        if (row["service"], row["date"]) == PLANTED
    )
    weekdays = [
        row["cost"]
        for row in records
        if row["service"] == PLANTED[0] and row["cost"] > 100 and row["date"] != PLANTED[1]
    ]
    assert min(weekdays) <= planted_cost <= max(weekdays), (
        "the planted cost sits outside the weekday range — the fixture proves "
        "nothing about seasonality, only about size"
    )


def test_a_flat_service_gains_no_rhythm_from_the_seasonal_switch(records):
    """Seasonality must not invent a weekly pattern where there is none."""
    control = [row for row in records if row["service"] == CONTROL]
    assert _scan(control, seasonal=False).anomalies == []
    assert _scan(control, seasonal=True).anomalies == []


def test_two_weeks_cannot_reach_the_seasonal_path(records):
    """The reason this fixture had to exist, asserted rather than asserted-to.

    Fourteen days give each weekday two samples; the guard refuses them and
    the scan falls back to the flat baseline. Nothing is broken — it is just
    never the seasonal code that runs.
    """
    fortnight = sorted({row["date"] for row in records})[-14:]
    short = [row for row in records if row["date"] in fortnight]
    run = run_detection(short, threshold=2.0, window=14, seasonal=True)
    assert all(not a.detector.endswith("+weekday") for a in run.anomalies)


# --- and through the product, not just the function ---------------------------


def test_the_seasonal_estate_serves_over_the_api(client, monkeypatch):
    """The file lane carries the fixture end to end, switch and all."""
    monkeypatch.setenv("SENTINEL_COSTS_FILE", str(FIXTURE))
    monkeypatch.setenv("SENTINEL_BASELINE_WINDOW_DAYS", str(WINDOW))

    monkeypatch.setenv("SENTINEL_SEASONAL", "0")
    flat = client.get("/anomalies")
    assert flat.status_code == 200
    assert flat.json()["anomalies"] == []

    monkeypatch.setenv("SENTINEL_SEASONAL", "1")
    seasonal = client.get("/anomalies").json()
    assert [(a["service"], a["date"]) for a in seasonal["anomalies"]] == [PLANTED]
    assert seasonal["anomalies"][0]["detector"] == "zscore+weekday"

    # and the lane says where the numbers came from
    assert client.get("/health").json()["data_sources"]["costs"] == "file"
