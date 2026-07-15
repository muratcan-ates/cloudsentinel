"""Performance budget checks over the mock-data pipeline (Sprint 2).

The budgets are deliberately generous: they exist to catch order-of-magnitude
regressions (an accidental quadratic loop, a per-request dataset reload gone
wrong, an unindexed hot query) — not to measure micro-variance between
machines. All timings run on the deterministic fake LLM provider, so the
numbers reflect our code, not network latency.
"""

import time

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def elapsed(calls):
    """Run the callables back to back and return total wall-clock seconds."""
    start = time.perf_counter()
    for call in calls:
        response = call()
        assert response.status_code == 200
    return time.perf_counter() - start


def test_detection_scan_budget():
    """20 consecutive full scans (detect + persist + respond) stay under 2s."""
    total = elapsed([lambda: client.get("/anomalies?threshold=2.0")] * 20)
    assert total < 2.0, f"20 scans took {total:.2f}s"


def test_cost_aggregation_budget():
    """20 summaries and 20 daily series stay under 2s combined."""
    total = elapsed(
        [lambda: client.get("/costs/summary")] * 20
        + [lambda: client.get("/costs/daily")] * 20
    )
    assert total < 2.0, f"40 aggregation calls took {total:.2f}s"


def test_pulse_full_chain_budget():
    """One pulse drives detect → analyst → debate → recommender → inbox.

    The whole chain, including SQLite writes and both agents on the fake
    provider, must finish well inside interactive latency.
    """
    total = elapsed([lambda: client.post("/pulse")])
    assert total < 3.0, f"pulse took {total:.2f}s"


def test_csv_export_budget():
    """10 CSV exports stay under 1.5s."""
    total = elapsed([lambda: client.get("/costs/summary/export")] * 10)
    assert total < 1.5, f"10 exports took {total:.2f}s"
