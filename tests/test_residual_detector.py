"""The forecast-residual scorer — the registry's answer to a trend.

The flat scorers (z-score, MAD, and either with leave-one-out) all measure
distance from an average. On a service whose spend is genuinely climbing,
the trend itself is most of the standard deviation, so the spread grows
wide enough to swallow a real spike — and the whole window looks
simultaneously unremarkable and slightly off.

This scorer fits the trend first and measures the deviation from where the
series was heading. It is a second opinion, not a replacement: it is worse
than MAD on a contaminated baseline, which is exactly why the backtest
reports every scorer side by side rather than picking one.
"""

import random
from datetime import date, timedelta

import pytest

from app.detection import (
    DETECTORS,
    MIN_RESIDUAL_POINTS,
    _ols_fitted,
    detector_mode,
    run_detection,
)


def trending(
    *,
    days: int = 28,
    base: float = 100.0,
    growth: float = 25.0,
    noise: float = 4.0,
    bump_at: int | None = 20,
    bump: float = 180.0,
    seed: int = 7,
    service: str = "svc",
) -> tuple[list[dict], str | None]:
    """A climbing series with an optional genuine spike planted on top."""
    rng = random.Random(seed)
    records = []
    for index in range(days):
        day = date(2026, 6, 1) + timedelta(days=index)
        records.append(
            {
                "service": service,
                "date": day.isoformat(),
                "cost": round(base + growth * index + rng.uniform(-noise, noise), 2),
            }
        )
    if bump_at is None:
        return records, None
    records[bump_at]["cost"] = round(records[bump_at]["cost"] + bump, 2)
    return records, records[bump_at]["date"]


def scan(records, *, detector="residual", loo=False, threshold=2.0):
    return run_detection(
        records,
        threshold,
        detector=detector,
        window=len(records),
        leave_one_out=loo,
    )


# --- the registry ------------------------------------------------------------


def test_residual_is_a_registry_member(monkeypatch):
    assert "residual" in DETECTORS
    monkeypatch.setenv("SENTINEL_DETECTOR", "residual")
    assert detector_mode() == "residual"


def test_an_unknown_detector_still_falls_back_to_zscore(monkeypatch):
    monkeypatch.setenv("SENTINEL_DETECTOR", "kalman")
    assert detector_mode() == "zscore"


# --- what it is for ----------------------------------------------------------


def test_it_catches_a_spike_the_flat_scorers_miss(subtests=None):
    """The claim, measured: 25/day of growth hides a 180-unit jump."""
    records, planted = trending()

    caught = scan(records).anomalies
    assert [anomaly.date for anomaly in caught] == [planted]

    for flat, loo in (("zscore", False), ("mad", False), ("zscore", True)):
        missed = scan(records, detector=flat, loo=loo).anomalies
        assert planted not in {a.date for a in missed}, (
            f"{flat}{'+loo' if loo else ''} was expected to miss the trend spike"
        )


def test_a_clean_trend_with_no_spike_stays_quiet():
    """Growth on its own is not an anomaly — that is the entire point."""
    records, _ = trending(bump_at=None)
    assert scan(records).anomalies == []


def test_it_is_not_universally_better_than_mad():
    """A contaminated baseline is still MAD's win; this scorer does not claim it."""
    records = []
    rng = random.Random(3)
    for index in range(28):
        day = date(2026, 6, 1) + timedelta(days=index)
        records.append(
            {
                "service": "svc",
                "date": day.isoformat(),
                "cost": round(100 + rng.uniform(-4, 4), 2),
            }
        )
    records[5]["cost"] = 2000.0  # the contaminating spike
    records[20]["cost"] = 300.0  # the smaller one it hides

    residual = {a.date for a in scan(records).anomalies}
    mad = {a.date for a in scan(records, detector="mad").anomalies}

    assert records[20]["date"] in mad
    assert records[20]["date"] not in residual


# --- what it reports ---------------------------------------------------------


def test_the_card_names_the_scorer_that_produced_it():
    records, _ = trending()
    assert scan(records).anomalies[0].detector == "residual"
    assert scan(records, loo=True).anomalies[0].detector == "residual+loo"


def test_the_reported_baseline_is_the_expected_cost_not_the_residual_mean():
    """A residual mean is ~0 and would be meaningless beside a cost figure."""
    records, planted = trending()
    anomaly = scan(records).anomalies[0]

    index = next(i for i, r in enumerate(records) if r["date"] == planted)
    expected_without_the_bump = records[index]["cost"] - 180.0

    assert anomaly.cost == records[index]["cost"]
    # the fitted value sits near where the series was actually heading
    assert abs(anomaly.service_mean - expected_without_the_bump) < 60
    assert anomaly.service_mean > 0


# --- degradation -------------------------------------------------------------


def test_a_group_too_small_to_fit_falls_back_and_says_so():
    """Weekday bucketing can hand the fit fewer points than a line needs.

    Three weeks of history is three samples per weekday — enough for a
    seasonal baseline, one short of a trend line. The scan keeps working
    and the label admits which scorer actually ran.
    """
    records = []
    for index in range(21):
        day = date(2026, 6, 1) + timedelta(days=index)
        records.append(
            {
                "service": "svc",
                "date": day.isoformat(),
                "cost": 100.0 + (60.0 if day.weekday() >= 5 else 0.0),
            }
        )
    records[16]["cost"] = 400.0  # a Wednesday spike

    run = run_detection(
        records, 1.0, detector="residual", window=21, seasonal=True
    )

    assert [anomaly.date for anomaly in run.anomalies] == [records[16]["date"]]
    assert run.anomalies[0].detector == "residual->zscore+weekday"


def test_the_fit_chases_an_outlier_sitting_at_the_edge_of_the_window():
    """The scorer's own weakness, asserted rather than left for a jury to find.

    A spike on the LAST day of a short window drags the trend line up to
    meet it, which shrinks its own residual — so the flat scorers catch it
    and this one does not. It is the mirror image of the trending-growth
    case, and the reason the backtest reports every scorer instead of
    crowning one.
    """
    records = [
        {"service": "svc", "date": f"2026-07-{index + 1:02d}", "cost": cost}
        for index, cost in enumerate([100.0] * 6 + [400.0])
    ]

    assert scan(records, detector="zscore").anomalies
    assert scan(records).anomalies == []


def test_the_fit_refuses_a_window_it_cannot_read():
    assert _ols_fitted([]) is None
    assert _ols_fitted([1.0] * (MIN_RESIDUAL_POINTS - 1)) is None
    fitted = _ols_fitted([1.0, 2.0, 3.0, 4.0])
    assert fitted is not None
    assert [round(value, 6) for value in fitted] == [1.0, 2.0, 3.0, 4.0]


def test_the_fit_is_a_least_squares_line_not_a_smoother():
    """Exact on a straight series, and it interpolates rather than follows."""
    fitted = _ols_fitted([0.0, 10.0, 20.0, 1000.0, 40.0])
    assert fitted is not None
    # a single outlier tilts the line but does not become the line
    assert fitted[3] < 1000.0


@pytest.mark.parametrize("poison", [float("nan"), float("inf"), "n/a", None])
def test_a_poisoned_record_cannot_reach_the_fit(poison):
    records, planted = trending()
    records.append({"service": "svc", "date": "2026-07-15", "cost": poison})
    run = scan(records)
    assert run.unusable_records == 1
    assert planted in {anomaly.date for anomaly in run.anomalies}


def test_a_mission_may_select_it():
    """The registry widening is real: the mission DSL accepts the new name."""
    from app.missions import MissionDetection

    spec = MissionDetection(
        source="cost",
        threshold=2.0,
        critical_z=3.0,
        detector="residual",
        baseline_window_days=28,
        seasonal=False,
    )
    assert spec.detector == "residual"
