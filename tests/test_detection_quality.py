"""Tests for the Sprint 3 detection-quality package.

Every claim the detection layer makes is asserted against synthetic
ground truth from the benchmark harness: rolling baseline windows,
minimum history, the MAD detector's contamination resistance, weekday
seasonality, the detector registry, and the /metrics/detection
precision proxy fed by operator verdicts.
"""

import pytest
from fastapi.testclient import TestClient

from app import db
from app.benchmark import build_scenario, evaluate
from app.detection import run_detection
from tests.test_analytics import run_chain
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# --- rolling window + minimum history -------------------------------------------


def test_rolling_window_scores_only_recent_records():
    scenario = build_scenario("windowed", days=40, spikes=((5, 8.0), (35, 5.0)))
    run = run_detection(scenario.records, 2.0, window=28)
    flagged = {(a.service, a.date) for a in run.anomalies}
    recent_spike = ("svc", scenario.records[35]["date"])
    old_spike = ("svc", scenario.records[5]["date"])
    assert recent_spike in flagged
    assert old_spike not in flagged  # outside the window: neither scored nor baseline
    assert all(a.detector_params["window_days"] == 28 for a in run.anomalies)


def test_short_history_is_reported_not_flagged():
    records = [
        {"service": "newborn", "date": f"2026-07-0{i + 1}", "cost": cost}
        for i, cost in enumerate([10.0, 11.0, 9.0, 10.5, 500.0])
    ]
    run = run_detection(records, 2.0)
    assert run.anomalies == []
    assert run.insufficient_data_services == ["newborn"]


def test_exactly_min_history_records_are_scored():
    """The MIN_HISTORY gate is `<`, not `<=`: seven records is a baseline."""
    costs = [100.0] * 6 + [400.0]
    records = [
        {"service": "seven", "date": f"2026-07-0{i + 1}", "cost": cost}
        for i, cost in enumerate(costs)
    ]
    run = run_detection(records, 2.0)
    assert run.insufficient_data_services == []
    assert [(a.service, a.date) for a in run.anomalies] == [("seven", "2026-07-07")]

    six = run_detection(records[:6], 2.0)
    assert six.insufficient_data_services == ["seven"]


def test_window_boundary_is_exact():
    """The oldest in-window record is scored; one record older is not.

    Nine consecutive days, window of eight: the day just inside the window
    is a real spike that must be flagged, and the day just outside is a
    poison value that must stay out of the baseline. Both off-by-one
    directions change the outcome.
    """
    costs = [10000.0, 400.0] + [100.0] * 7
    records = [
        {"service": "edge", "date": f"2026-07-{i + 1:02d}", "cost": cost}
        for i, cost in enumerate(costs)
    ]
    run = run_detection(records, 2.0, window=8)
    assert [(a.service, a.date) for a in run.anomalies] == [("edge", "2026-07-02")]


def test_stale_service_ages_out_of_the_calendar_window():
    """The window is calendar days anchored to the dataset's newest date, so
    a service whose data stopped long ago is reported as insufficient, not
    scored against fossil records."""
    fresh = [
        {"service": "fresh", "date": f"2026-07-{i + 1:02d}", "cost": 100.0 + i}
        for i in range(14)
    ]
    stale = [
        {"service": "stale", "date": f"2026-05-{i + 1:02d}", "cost": 100.0 + i}
        for i in range(14)
    ]
    run = run_detection(fresh + stale, 2.0)
    assert run.insufficient_data_services == ["stale"]


# --- detector quality claims (benchmark-backed) ----------------------------------


def test_clean_spikes_are_caught_by_both_detectors():
    scenario = build_scenario("clean", spikes=((10, 5.0), (20, 6.0)))
    for detector in ("zscore", "mad"):
        result = evaluate(scenario, threshold=2.0, detector=detector)
        assert result.recall == 1.0
        assert result.false_positives == 0


def test_mad_survives_the_contaminated_baseline_zscore_misses():
    """One huge spike inflates mean/stdev and blinds the classic z-score to
    the second, smaller spike; the median/MAD baseline catches both."""
    scenario = build_scenario("contaminated", spikes=((5, 20.0), (20, 3.0)))
    zscore = evaluate(scenario, threshold=2.0, detector="zscore")
    mad = evaluate(scenario, threshold=2.0, detector="mad")
    assert zscore.recall == 0.5  # only the huge spike
    assert mad.recall == 1.0
    assert mad.false_positives == 0


def test_weekday_baseline_removes_weekend_false_positives():
    scenario = build_scenario(
        "weekend", days=42, noise=0.0, weekend_uplift=150.0, spikes=((17, 3.0),)
    )
    flat = evaluate(scenario, threshold=1.4, detector="zscore", seasonal=False)
    seasonal = evaluate(scenario, threshold=1.4, detector="zscore", seasonal=True)
    assert flat.false_positives > 0  # weekends read as anomalies on a flat baseline
    assert flat.recall == 1.0
    assert seasonal.false_positives == 0
    assert seasonal.recall == 1.0


def test_seasonal_falls_back_when_buckets_cannot_reach_the_threshold():
    """The critical blindness case: a 28-day window gives 4-sample weekday
    buckets, whose self-inclusive pstdev caps |z| at sqrt(3) < 2.0 — with
    seasonality requested, detection must fall back to the flat baseline
    and still catch a 100x spike at the default threshold."""
    scenario = build_scenario(
        "blind", days=28, noise=2.0, weekend_uplift=150.0, spikes=((16, 100.0),)
    )
    run = run_detection(scenario.records, 2.0, detector="zscore", seasonal=True)
    flagged = {(a.service, a.date) for a in run.anomalies}
    assert scenario.planted <= flagged
    assert all(a.detector_params["seasonal"] is False for a in run.anomalies)


def test_seasonal_applies_at_exactly_min_weekday_samples():
    """21 days = 3 samples per weekday bucket, the gate's floor; at a low
    threshold the buckets are usable and the seasonal registry proves it."""
    records = []
    for i in range(21):
        day = f"2026-06-{i + 1:02d}"
        weekday = i % 7  # 2026-06-01 is a Monday, so index i has weekday i%7
        cost = 250.0 if weekday >= 5 else 100.0
        records.append({"service": "svc", "date": day, "cost": cost})
    records[16]["cost"] = 300.0  # a Wednesday spike inside its flat bucket
    run = run_detection(records, 1.0, detector="zscore", seasonal=True, window=21)
    assert any(a.date == "2026-06-17" for a in run.anomalies)
    assert all(a.detector_params["seasonal"] is True for a in run.anomalies)


def test_mad_with_zero_spread_falls_back_to_zscore():
    """Over half the window identical -> MAD is 0; the spike must still be
    caught, honestly labeled as the fallback."""
    records = [
        {"service": "flatmed", "date": f"2026-06-{i + 1:02d}", "cost": 100.0}
        for i in range(27)
    ] + [{"service": "flatmed", "date": "2026-06-28", "cost": 1000.0}]
    run = run_detection(records, 2.0, detector="mad")
    assert [(a.date, a.detector) for a in run.anomalies] == [
        ("2026-06-28", "mad->zscore")
    ]


# --- configuration parsing -------------------------------------------------------


def test_garbage_env_config_degrades_to_defaults(monkeypatch):
    monkeypatch.setenv("SENTINEL_DETECTOR", "quantum")
    monkeypatch.setenv("SENTINEL_BASELINE_WINDOW_DAYS", "3")  # below MIN_HISTORY
    records = [
        {"service": "svc", "date": f"2026-06-{i + 1:02d}", "cost": 100.0 + i}
        for i in range(10)
    ]
    run = run_detection(records, 5.0)
    assert run.detector == "zscore"
    assert run.window_days == 28


# --- detector registry on the wire -----------------------------------------------


def test_anomaly_report_carries_the_detector_registry(client):
    body = client.get("/anomalies").json()
    assert body["detector"] == "zscore"
    assert body["window_days"] == 28
    assert body["insufficient_data_services"] == []
    for anomaly in body["anomalies"]:
        assert anomaly["detector"] == "zscore"
        assert anomaly["detector_params"]["min_history"] == 7
        assert anomaly["detector_params"]["seasonal"] is False


def test_mad_mode_still_finds_the_planted_mock_spikes(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_DETECTOR", "mad")
    body = client.get("/anomalies").json()
    assert body["detector"] == "mad"
    flagged = {(a["service"], a["date"]) for a in body["anomalies"]}
    assert {("compute", "2026-06-29"), ("database", "2026-07-02")} <= flagged


# --- /metrics/detection ----------------------------------------------------------


def test_metrics_detection_empty_database(client):
    body = client.get("/metrics/detection").json()
    assert body["decided"] == 0
    assert body["precision_proxy"] is None
    assert body["services"] == []
    assert "proxy" in body["method"]


def test_metrics_detection_reads_rejects_as_false_positives(client):
    """Asymmetric split on purpose: 2 approvals + 1 rejection reads 0.6667,
    so a numerator swap (the rejection rate) cannot hide behind 0.5."""
    run_chain(client, service="compute", occurred_on="2026-07-01", verdict="approve")
    run_chain(client, service="compute", occurred_on="2026-07-03", verdict="approve")
    run_chain(client, service="storage", occurred_on="2026-07-02", verdict="reject")
    body = client.get("/metrics/detection").json()
    assert body["approved"] == 2
    assert body["rejected"] == 1
    assert body["decided"] == 3
    assert body["precision_proxy"] == round(2 / 3, 4)
    rows = {row["service"]: row for row in body["services"]}
    assert rows["compute"]["precision_proxy"] == 1.0
    assert rows["storage"]["precision_proxy"] == 0.0


def test_metrics_detection_window_excludes_old_verdicts(client):
    conn = db.connect()
    try:
        with db.writing(conn):
            conn.execute(
                "INSERT INTO decisions (action_id, service, verdict, rationale, "
                "input_context_json, created_at) VALUES (NULL, 'ancient', "
                "'rejected', NULL, '{}', datetime('now', '-40 days'))"
            )
    finally:
        conn.close()
    assert client.get("/metrics/detection").json()["decided"] == 0  # default 30d
    wide = client.get("/metrics/detection", params={"window_days": 60}).json()
    assert wide["decided"] == 1
    assert wide["rejected"] == 1


def test_metrics_detection_window_days_is_bounded(client):
    assert client.get("/metrics/detection", params={"window_days": 0}).status_code == 422
    assert client.get("/metrics/detection", params={"window_days": 366}).status_code == 422
    # the documented bounds themselves are accepted
    assert client.get("/metrics/detection", params={"window_days": 1}).status_code == 200
    assert client.get("/metrics/detection", params={"window_days": 365}).status_code == 200
