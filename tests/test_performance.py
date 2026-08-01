"""Performance budget checks over the mock-data pipeline (Sprint 2).

The budgets are deliberately generous: they exist to catch order-of-magnitude
regressions (an accidental quadratic loop, a per-request dataset reload gone
wrong, an unindexed hot query) — not to measure micro-variance between
machines. All timings run on the deterministic fake LLM provider, so the
numbers reflect our code, not network latency.

The reflex gates below assert a PERCENTILE rather than a total. A
total-of-N budget is a mean in disguise: one pathological call hides
inside nineteen quick ones, and the reflex path is exactly where that
matters, because "REFLEX X ms" is a claim the dashboard makes out loud.
"""

import time

from fastapi.testclient import TestClient

from app.benchmark import (
    REFLEX_P95_BUDGET_MS,
    REFLEX_SCALE_P95_BUDGET_MS,
    SCALE_DAYS,
    SCALE_SERVICES,
    measure,
    percentile,
    reflex_latency_profile,
    scale_records,
)
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


# --- reflex latency gates ---------------------------------------------------


def test_percentile_reports_an_observed_value():
    """Nearest-rank, so a failing gate always names a real measurement."""
    sample = [5.0, 1.0, 3.0, 2.0, 4.0]
    assert percentile(sample, 0.5) == 3.0
    assert percentile(sample, 0.95) == 5.0
    assert percentile(sample, 1.0) == 5.0
    # A single sample is its own every percentile.
    assert percentile([7.0], 0.95) == 7.0


def test_the_gate_would_catch_a_slow_tail_a_mean_gate_hides():
    """Guard the guard: eighteen fast calls must not absolve a slow tail."""
    timings = [1.0] * 18 + [200.0] * 2
    assert sum(timings) / len(timings) < 30.0  # a total/mean budget passes
    assert percentile(timings, 0.95) == 200.0  # the p95 gate does not


def test_a_lone_outlier_is_the_p100_and_the_profile_reports_it():
    """One slow call in twenty is genuinely not the 95th percentile — which
    is why every profile publishes max_ms beside p95_ms rather than
    pretending the percentile catches everything."""
    timings = [1.0] * 19 + [500.0]
    assert percentile(timings, 0.95) == 1.0
    assert max(timings) == 500.0


def test_reflex_p95_budget():
    """The fast lane stays fast on the shipped estate."""
    profile = reflex_latency_profile(samples=120)
    assert profile.p95_ms < REFLEX_P95_BUDGET_MS, (
        f"reflex p95 {profile.p95_ms}ms exceeds the "
        f"{REFLEX_P95_BUDGET_MS}ms budget (p50 {profile.p50_ms}ms, "
        f"max {profile.max_ms}ms)"
    )


def test_reflex_p95_budget_at_estate_scale():
    """A year of history for a larger estate — where a quadratic shows up.

    The shipped fixture is 56 records; this is 2920. An accidental
    per-record rescan is invisible in the first and unmissable here.
    """
    profile = reflex_latency_profile(
        scale_records(), label="reflex-scale", samples=30
    )
    assert profile.p95_ms < REFLEX_SCALE_P95_BUDGET_MS, (
        f"reflex p95 {profile.p95_ms}ms over {SCALE_SERVICES}x{SCALE_DAYS} "
        f"records exceeds the {REFLEX_SCALE_P95_BUDGET_MS}ms budget "
        f"(p50 {profile.p50_ms}ms, max {profile.max_ms}ms)"
    )


def test_reflex_scaling_stays_roughly_linear():
    """52x the records must not cost anything like 52-squared the time.

    The budgets above catch an absolute blowup; this catches the SHAPE of
    the regression directly, so a machine slow enough to inflate both
    figures still fails for the right reason.
    """
    small = reflex_latency_profile(samples=60)
    large = reflex_latency_profile(scale_records(), label="scale", samples=20)
    record_ratio = (SCALE_SERVICES * SCALE_DAYS) / 56
    # Generous: linear would be ~52x, quadratic ~2700x. Anything under 10x
    # the record ratio is comfortably not quadratic.
    assert large.p95_ms < small.p95_ms * record_ratio * 10, (
        f"reflex scaled {large.p95_ms / max(small.p95_ms, 1e-6):.0f}x for "
        f"{record_ratio:.0f}x the records — that shape is not linear"
    )


def test_http_scan_p95_budget():
    """The same reflex through the HTTP layer, percentile-gated."""
    profile = measure(
        "http-scan", lambda: client.get("/anomalies?threshold=2.0"), samples=40
    )
    assert profile.p95_ms < 250.0, f"scan p95 {profile.p95_ms}ms"
