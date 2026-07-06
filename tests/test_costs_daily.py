"""Tests for the GET /costs/daily trend series endpoint."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

MOCK_DATA = json.loads((Path(__file__).parent.parent / "data" / "mock_costs.json").read_text())


def get_report():
    response = client.get("/costs/daily")
    assert response.status_code == 200
    return response.json()


def test_dates_are_sorted_and_unique():
    report = get_report()
    dates = report["dates"]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))


def test_every_series_is_aligned_to_the_date_axis():
    report = get_report()
    day_count = len(report["dates"])
    assert day_count > 0
    for series in report["services"]:
        assert len(series["values"]) == day_count
    assert len(report["totals"]) == day_count


def test_totals_are_the_sum_of_service_values():
    report = get_report()
    for i, total in enumerate(report["totals"]):
        assert total == round(sum(s["values"][i] for s in report["services"]), 2)


def test_values_match_the_source_records():
    report = get_report()
    dates = report["dates"]
    series = {s["service"]: s["values"] for s in report["services"]}
    for record in MOCK_DATA["daily_costs"]:
        assert series[record["service"]][dates.index(record["date"])] == record["cost"]


def test_metadata_matches_the_dataset():
    report = get_report()
    assert report["currency"] == MOCK_DATA["currency"]
    assert report["period"] == MOCK_DATA["period"]


def test_service_names_match_the_cost_summary():
    summary = client.get("/costs/summary").json()
    report = get_report()
    assert {s["service"] for s in report["services"]} == {
        s["service"] for s in summary["services"]
    }
