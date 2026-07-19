"""GET /metrics/backtest — precision/recall against planted ground truth."""

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_backtest_reports_every_mode_and_scenario(client):
    data = client.get("/metrics/backtest").json()
    assert {row["mode"] for row in data["rows"]} == {"zscore", "mad", "zscore+loo"}
    assert "contaminated-baseline" in {row["scenario"] for row in data["rows"]}


def test_backtest_shows_mad_beats_zscore_on_contamination(client):
    rows = client.get("/metrics/backtest").json()["rows"]

    def recall(scenario, mode):
        return next(
            row["recall"]
            for row in rows
            if row["scenario"] == scenario and row["mode"] == mode
        )

    # The documented claim, now measured: MAD keeps full recall where the
    # classic z-score misses the smaller spike under a contaminated baseline.
    assert recall("contaminated-baseline", "mad") > recall(
        "contaminated-baseline", "zscore"
    )
