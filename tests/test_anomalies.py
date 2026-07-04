"""Tests for the anomaly detection endpoint and core logic."""

from fastapi.testclient import TestClient

from main import app, detect_anomalies

client = TestClient(app)


def test_default_threshold_detects_planted_spikes():
    response = client.get("/anomalies")
    assert response.status_code == 200
    body = response.json()
    assert body["anomaly_count"] == 2
    flagged = {(a["service"], a["date"]) for a in body["anomalies"]}
    assert flagged == {("compute", "2026-06-29"), ("database", "2026-07-02")}
    assert all(a["severity"] == "critical" for a in body["anomalies"])


def test_records_analyzed_covers_full_dataset():
    body = client.get("/anomalies").json()
    assert body["records_analyzed"] == 56


def test_anomalies_sorted_by_absolute_z_score():
    body = client.get("/anomalies?threshold=1.5").json()
    z_scores = [abs(a["z_score"]) for a in body["anomalies"]]
    assert z_scores == sorted(z_scores, reverse=True)


def test_lower_threshold_flags_superset():
    strict = client.get("/anomalies?threshold=2.0").json()
    loose = client.get("/anomalies?threshold=1.5").json()
    strict_set = {(a["service"], a["date"]) for a in strict["anomalies"]}
    loose_set = {(a["service"], a["date"]) for a in loose["anomalies"]}
    assert strict_set <= loose_set
    assert loose["anomaly_count"] >= strict["anomaly_count"]


def test_high_threshold_returns_no_anomalies():
    body = client.get("/anomalies?threshold=5").json()
    assert body["anomaly_count"] == 0
    assert body["anomalies"] == []


def test_invalid_thresholds_are_rejected():
    for value in ["0", "-1", "abc", "inf", "nan"]:
        response = client.get(f"/anomalies?threshold={value}")
        assert response.status_code == 422, value
def test_service_filter_returns_only_matching_service():
    body = client.get("/anomalies?threshold=1.5&service=compute").json()
    assert body["anomaly_count"] > 0
    assert all(a["service"] == "compute" for a in body["anomalies"])


def test_service_filter_is_case_insensitive():
    body = client.get("/anomalies?threshold=1.5&service=COMPUTE").json()
    assert body["anomaly_count"] > 0
    assert all(a["service"] == "compute" for a in body["anomalies"])


def test_service_filter_unknown_service_returns_empty():
    body = client.get("/anomalies?threshold=1.5&service=doesnotexist").json()
    assert body["anomaly_count"] == 0
    assert body["anomalies"] == []


def test_service_filter_does_not_change_records_analyzed():
    unfiltered = client.get("/anomalies?threshold=1.5").json()
    filtered = client.get("/anomalies?threshold=1.5&service=compute").json()
    assert filtered["records_analyzed"] == unfiltered["records_analyzed"]

def test_zero_stdev_service_is_skipped():
    records = [
        {"date": f"2026-06-{20 + i:02d}", "service": "flatline", "cost": 10.0}
        for i in range(5)
    ]
    assert detect_anomalies(records, threshold=2.0) == []
