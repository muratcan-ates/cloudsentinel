"""Tests for the cost summary endpoint and aggregation logic."""

from fastapi.testclient import TestClient

from main import app, load_daily_costs, summarize_costs

client = TestClient(app)


def test_summary_covers_full_dataset():
    response = client.get("/costs/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["records_analyzed"] == 56
    assert body["currency"] == "USD"


def test_services_sorted_by_total_cost_descending():
    body = client.get("/costs/summary").json()
    totals = [s["total_cost"] for s in body["services"]]
    assert totals == sorted(totals, reverse=True)


def test_shares_sum_to_one():
    body = client.get("/costs/summary").json()
    assert abs(sum(s["share_of_total"] for s in body["services"]) - 1.0) < 0.01


def test_total_cost_matches_service_totals():
    body = client.get("/costs/summary").json()
    assert abs(body["total_cost"] - sum(s["total_cost"] for s in body["services"])) < 0.01


def test_summarize_costs_per_service_bounds():
    summaries = summarize_costs(load_daily_costs())
    for summary in summaries:
        assert summary.min_daily_cost <= summary.mean_daily_cost <= summary.max_daily_cost


def test_compute_service_exact_aggregates():
    compute = next(
        s for s in summarize_costs(load_daily_costs()) if s.service == "compute"
    )
    assert compute.total_cost == 2771.7
    assert compute.mean_daily_cost == 197.98
    assert compute.min_daily_cost == 117.5
    assert compute.max_daily_cost == 1183.4
    assert compute.share_of_total == 0.5701


def test_summarize_costs_empty_input():
    assert summarize_costs([]) == []
