"""Property-based tests for the detector (Hypothesis).

The hand-written detection tests assert what we thought to check. These
assert invariants that must hold for EVERY input Hypothesis can build:
generated NaN and infinities, duplicated rows, denormal and near-overflow
magnitudes, and windows fed in reverse. The generator found a real defect
on its first run — a single NaN cost anywhere in a service's history
crashed the whole scan inside ``statistics.pstdev`` — which is exactly
what this file exists for.

Invariants under test:

- the scan never raises, whatever a data source hands it;
- nothing unmeasurable reaches the inbox (every published score is finite,
  and clears the threshold it was scanned at);
- severity always agrees with the score the reader can recompute;
- the result does not depend on the order records arrive in;
- a service without enough history is reported, never scored;
- raising the threshold can only shrink the set of anomalies.
"""

import math

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.detection import (
    CRITICAL_Z_SCORE,
    MIN_HISTORY,
    run_detection,
)

# The autouse database fixture in conftest is function-scoped, and
# Hypothesis rightly warns that it is not reset between examples. Detection
# is a pure statistics pass that never touches the database, so the fixture
# is irrelevant here rather than unsound.
PURE = settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

SERVICES = st.sampled_from(["compute", "database", "network", "storage"])

# Ordinary money-shaped values, plus the magnitudes that break naive
# arithmetic: denormals, near-overflow, and the non-finite trio a feed or a
# malformed CSV can genuinely produce.
ORDINARY_COSTS = st.floats(
    min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False
)
HOSTILE_COSTS = st.one_of(
    ORDINARY_COSTS,
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
    st.just(5e-324),
    st.just(1.7e308),
    st.just(-1e6),
    st.none(),
    st.text(max_size=4),
)
THRESHOLDS = st.floats(
    min_value=0.5, max_value=8.0, allow_nan=False, allow_infinity=False
)


def series(costs: list, service: str = "compute", day_offset: int = 0) -> list[dict]:
    """One service's daily records over consecutive dates."""
    return [
        {
            "service": service,
            # July 2026 has 31 days; the generators stay well inside it.
            "date": f"2026-07-{(index + day_offset) % 28 + 1:02d}",
            "cost": cost,
        }
        for index, cost in enumerate(costs)
    ]


records_strategy = st.builds(
    series,
    costs=st.lists(HOSTILE_COSTS, max_size=40),
    service=SERVICES,
)


# --- it never falls over -----------------------------------------------------


@PURE
@given(records=records_strategy, threshold=THRESHOLDS)
def test_the_scan_never_raises(records, threshold):
    """Whatever a source hands us, the answer is a report — not a 500."""
    run_detection(records, threshold)


@PURE
@given(
    costs=st.lists(ORDINARY_COSTS, min_size=MIN_HISTORY, max_size=25),
    poison=st.sampled_from([float("nan"), float("inf"), float("-inf"), None, "n/a"]),
    threshold=THRESHOLDS,
)
def test_one_poisoned_record_cannot_take_down_its_service(costs, poison, threshold):
    """A single NaN used to crash the scan inside statistics.pstdev."""
    clean = run_detection(series(costs), threshold)
    poisoned = run_detection(series([*costs, poison]), threshold)

    assert poisoned.unusable_records == 1
    # the surviving history still scores exactly as it did on its own
    assert {(a.service, a.date, a.z_score) for a in clean.anomalies} == {
        (a.service, a.date, a.z_score) for a in poisoned.anomalies
    }


# --- nothing unmeasurable reaches the inbox ----------------------------------


@PURE
@given(records=records_strategy, threshold=THRESHOLDS)
def test_every_published_figure_is_finite(records, threshold):
    """A NaN score compares False against every bound — it must never exist."""
    for anomaly in run_detection(records, threshold).anomalies:
        assert math.isfinite(anomaly.z_score)
        assert math.isfinite(anomaly.cost)
        assert math.isfinite(anomaly.service_mean)


@PURE
@given(records=records_strategy, threshold=THRESHOLDS)
def test_a_flagged_record_always_clears_its_threshold(records, threshold):
    for anomaly in run_detection(records, threshold).anomalies:
        assert abs(anomaly.z_score) >= threshold


@PURE
@given(records=records_strategy, threshold=THRESHOLDS)
def test_severity_agrees_with_the_score_a_reader_can_recompute(records, threshold):
    """The published (rounded) score decides severity — no disagreement band."""
    for anomaly in run_detection(records, threshold).anomalies:
        expected = (
            "critical" if abs(anomaly.z_score) >= CRITICAL_Z_SCORE else "warning"
        )
        assert anomaly.severity == expected


@PURE
@given(records=records_strategy, threshold=THRESHOLDS)
def test_every_anomaly_names_the_detector_that_produced_it(records, threshold):
    for anomaly in run_detection(records, threshold).anomalies:
        assert anomaly.detector
        assert anomaly.detector_params["min_history"] == MIN_HISTORY


# --- order and duplicates ----------------------------------------------------


@PURE
@given(records=records_strategy, threshold=THRESHOLDS)
def test_the_result_does_not_depend_on_arrival_order(records, threshold):
    """Newest-first, oldest-first or shuffled: the same window, the same math."""
    forward = run_detection(list(records), threshold)
    backward = run_detection(list(reversed(records)), threshold)

    def key(run):
        return sorted(
            (a.service, a.date, a.cost, a.z_score, a.severity) for a in run.anomalies
        )

    assert key(forward) == key(backward)
    assert forward.insufficient_data_services == backward.insufficient_data_services
    assert forward.unusable_records == backward.unusable_records


@PURE
@given(
    cost=ORDINARY_COSTS,
    length=st.integers(min_value=MIN_HISTORY, max_value=28),
    threshold=THRESHOLDS,
)
def test_a_flat_series_carries_no_signal(cost, length, threshold):
    """Identical costs have no spread; a deviation from nothing is not news."""
    run = run_detection(series([cost] * length), threshold)
    assert run.anomalies == []


@PURE
@given(
    costs=st.lists(ORDINARY_COSTS, min_size=MIN_HISTORY, max_size=20),
    threshold=THRESHOLDS,
)
def test_duplicated_rows_never_invent_a_service(costs, threshold):
    """The same day submitted twice must not conjure a service or a crash."""
    doubled = series(costs) + series(costs)
    run = run_detection(doubled, threshold)
    assert {a.service for a in run.anomalies} <= {"compute"}


# --- history and thresholds --------------------------------------------------


@PURE
@given(
    costs=st.lists(ORDINARY_COSTS, min_size=1, max_size=MIN_HISTORY - 1),
    threshold=THRESHOLDS,
)
def test_thin_history_is_reported_never_scored(costs, threshold):
    """Two data points are not a baseline — say so instead of guessing."""
    run = run_detection(series(costs), threshold)
    assert run.insufficient_data_services == ["compute"]
    assert run.anomalies == []


@PURE
@given(
    costs=st.lists(ORDINARY_COSTS, min_size=MIN_HISTORY, max_size=25),
    low=THRESHOLDS,
    bump=st.floats(min_value=0.1, max_value=5.0, allow_nan=False, allow_infinity=False),
)
def test_raising_the_threshold_can_only_narrow_the_result(costs, low, bump):
    """The sensitivity slider must be monotonic, or the operator cannot trust it."""
    records = series(costs)
    loose = {(a.date, a.z_score) for a in run_detection(records, low).anomalies}
    strict = {(a.date, a.z_score) for a in run_detection(records, low + bump).anomalies}
    assert strict <= loose


@PURE
@given(
    costs=st.lists(ORDINARY_COSTS, min_size=MIN_HISTORY, max_size=25),
    threshold=THRESHOLDS,
)
def test_a_service_is_either_scored_or_reported_as_thin_never_both(costs, threshold):
    run = run_detection(series(costs), threshold)
    scored = {a.service for a in run.anomalies}
    assert scored.isdisjoint(set(run.insufficient_data_services))


# --- the empty case ----------------------------------------------------------


@pytest.mark.parametrize("records", [[], [{}], [{"service": "compute"}]])
def test_degenerate_input_answers_with_an_empty_report(records):
    run = run_detection(records, 2.0)
    assert run.anomalies == []
    assert run.insufficient_data_services == []
