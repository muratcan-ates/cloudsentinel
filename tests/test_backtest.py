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
    assert {row["mode"] for row in data["rows"]} == {
        "zscore",
        "mad",
        "zscore+loo",
        "residual",
        "residual+loo",
    }
    scenarios = {row["scenario"] for row in data["rows"]}
    assert "contaminated-baseline" in scenarios
    assert "trending-growth" in scenarios
    # every scorer is measured on every scenario — no quiet gaps in the grid
    assert len(data["rows"]) == len(scenarios) * 5


def test_backtest_shows_the_residual_scorer_beats_flat_baselines_on_a_trend(client):
    """The claim the fourth scenario exists to prove, asserted not narrated.

    Spend climbing 25/day inflates the flat baseline's spread so far that a
    genuine 180-unit spike sits inside it: z-score, MAD and leave-one-out
    all miss it. Fitting the trend first shrinks the spread back to the
    noise floor, where the same spike is unmissable.
    """
    rows = client.get("/metrics/backtest").json()["rows"]
    recall = {
        row["mode"]: row["recall"]
        for row in rows
        if row["scenario"] == "trending-growth"
    }
    assert recall["residual"] == 1.0
    assert recall["residual+loo"] == 1.0
    for flat in ("zscore", "mad", "zscore+loo"):
        assert recall[flat] == 0.0, f"{flat} unexpectedly caught the trend spike"


def test_no_scorer_wins_every_scenario(client):
    """Why five rows are reported side by side instead of one being chosen."""
    rows = client.get("/metrics/backtest").json()["rows"]
    modes = {row["mode"] for row in rows}
    perfect = {
        mode
        for mode in modes
        if all(
            row["recall"] == 1.0 for row in rows if row["mode"] == mode
        )
    }
    assert perfect == set(), f"{perfect} would make the others redundant"


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
