"""Synthetic benchmark harness for the detection layer (Sprint 3).

Detection quality cannot be argued from the shipped mock data alone —
it is too small and too clean. This harness builds deterministic
synthetic scenarios with PLANTED anomalies (ground truth known by
construction), runs a detector over them, and scores precision/recall.
No randomness leaks between runs: every scenario is seeded.

Used by ``scripts/benchmark_detection.py`` (human-readable table) and by
the test suite (the mad-vs-zscore contamination claim is asserted, not
just narrated).
"""

import math
import random
import time
from dataclasses import dataclass
from datetime import date, timedelta

from app.detection import run_detection


@dataclass
class Scenario:
    name: str
    records: list[dict]
    planted: set[tuple[str, str]]  # (service, date) ground truth


def build_scenario(
    name: str,
    *,
    days: int = 28,
    base: float = 100.0,
    noise: float = 4.0,
    weekend_uplift: float = 0.0,
    trend: float = 0.0,
    spikes: tuple[tuple[int, float], ...] = (),
    bumps: tuple[tuple[int, float], ...] = (),
    service: str = "svc",
    seed: int = 7,
    start: date = date(2026, 6, 1),
) -> Scenario:
    """One service's synthetic daily series with planted anomalies.

    ``trend`` adds a linear per-day drift, so a scenario can carry genuine
    growth rather than a flat baseline with noise.

    Two ways to plant ground truth, because a trending series needs the
    second one: ``spikes`` are (day_index, multiplier) pairs that OVERWRITE
    the record with ``base * multiplier``, and ``bumps`` are (day_index,
    amount) pairs ADDED on top of whatever the day already was — which is
    the only way to plant a spike without flattening the trend underneath
    it.
    """
    rng = random.Random(seed)
    records = []
    for index in range(days):
        day = start + timedelta(days=index)
        cost = base + trend * index + (rng.uniform(-noise, noise) if noise else 0.0)
        if weekend_uplift and day.weekday() >= 5:
            cost += weekend_uplift
        records.append(
            {"service": service, "date": day.isoformat(), "cost": round(cost, 2)}
        )
    planted = set()
    for index, multiplier in spikes:
        records[index]["cost"] = round(base * multiplier, 2)
        planted.add((service, records[index]["date"]))
    for index, amount in bumps:
        records[index]["cost"] = round(records[index]["cost"] + amount, 2)
        planted.add((service, records[index]["date"]))
    return Scenario(name=name, records=records, planted=planted)


@dataclass
class BenchmarkResult:
    scenario: str
    detector: str
    seasonal: bool
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float | None
    recall: float | None


def evaluate(
    scenario: Scenario,
    *,
    threshold: float = 2.0,
    detector: str = "zscore",
    seasonal: bool = False,
    window: int | None = None,
    leave_one_out: bool = False,
) -> BenchmarkResult:
    """Score one detector configuration against a scenario's ground truth."""
    run = run_detection(
        scenario.records,
        threshold,
        detector=detector,
        seasonal=seasonal,
        window=window or len(scenario.records),
        leave_one_out=leave_one_out,
    )
    flagged = {(anomaly.service, anomaly.date) for anomaly in run.anomalies}
    tp = len(flagged & scenario.planted)
    fp = len(flagged - scenario.planted)
    fn = len(scenario.planted - flagged)
    return BenchmarkResult(
        scenario=scenario.name,
        detector=detector,
        seasonal=seasonal,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=round(tp / (tp + fp), 4) if (tp + fp) else None,
        recall=round(tp / (tp + fn), 4) if (tp + fn) else None,
    )


def standard_scenarios() -> list[Scenario]:
    """The four claims the detection layer makes, as testable scenarios."""
    return [
        # 1. Clean baseline, two honest spikes: any detector should find both.
        build_scenario("clean-spikes", spikes=((10, 5.0), (20, 6.0))),
        # 2. Contaminated baseline: one huge spike inflates mean/stdev so the
        #    classic z-score goes blind to the second, smaller spike; the
        #    median/MAD baseline does not.
        build_scenario("contaminated-baseline", spikes=((5, 20.0), (20, 3.0))),
        # 3. Weekend seasonality: flat weekday spend, higher weekends, one
        #    genuine midweek spike — a flat baseline flags the weekends.
        build_scenario(
            "weekend-pattern",
            days=42,
            noise=0.0,
            weekend_uplift=150.0,
            spikes=((17, 3.0),),  # 2026-06-18, a Thursday
        ),
        # 4. Genuine growth: spend climbing 25/day with one real spike on top.
        #    The trend itself dominates the standard deviation, so the flat
        #    baseline's spread is far too wide to notice a 180-unit jump —
        #    z-score, MAD and leave-one-out all miss it. Fitting the trend
        #    first shrinks the spread back to the noise floor, where the
        #    spike is unmissable. This is the residual scorer's failure mode
        #    to own, and the reason a second scorer exists at all.
        build_scenario(
            "trending-growth",
            trend=25.0,
            bumps=((20, 180.0),),
        ),
    ]


# --- latency gates ----------------------------------------------------------
#
# The scoring above measures whether detection is CORRECT. Nothing measured
# whether it is still FAST, and the reflex path is the one place in this
# system where that is a product claim rather than a nicety: the dashboard
# wears a "REFLEX X ms" badge, and the dual-loop design justifies itself by
# the fast lane staying sub-millisecond on real estate sizes.
#
# A total-of-N-calls budget (what tests/test_performance.py used everywhere)
# is a mean in disguise: one pathological call hides inside nineteen quick
# ones. These gates assert a PERCENTILE, so a regression that only bites
# sometimes still breaks the build.
#
# Budgets are deliberately generous — two orders of magnitude above the
# figures this repo actually measures — because they exist to catch an
# accidental quadratic or a per-call dataset reload, not to referee
# micro-variance between a laptop and a CI runner.

# Measured on the shipped 4-service, 14-day fixture: p95 ~0.4 ms.
REFLEX_P95_BUDGET_MS = 25.0
# Same path over a year of history for a larger estate (8 services x 365
# days). Measured p95 ~3.4 ms; a quadratic loop is invisible on the shipped
# fixture and would land near a full second here, which is the point.
REFLEX_SCALE_P95_BUDGET_MS = 80.0
SCALE_SERVICES = 8
SCALE_DAYS = 365


@dataclass
class LatencyProfile:
    label: str
    samples: int
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile over an unsorted sample.

    Nearest-rank rather than interpolated: with the sample sizes here an
    interpolated p95 would invent a value between two measurements, and a
    budget gate should fail on a number that was actually observed.
    """
    if not values:
        raise ValueError("percentile of an empty sample")
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def measure(label: str, call, *, samples: int = 200, warmup: int = 5) -> LatencyProfile:
    """Time ``call`` repeatedly and report its latency distribution.

    The warmup runs are discarded: the first call through any path pays for
    lazy imports and a cold dataset cache, and charging that to the p95
    would make the gate a measure of module loading.
    """
    for _ in range(warmup):
        call()
    timings = []
    for _ in range(samples):
        started = time.perf_counter()
        call()
        timings.append((time.perf_counter() - started) * 1000)
    return LatencyProfile(
        label=label,
        samples=len(timings),
        mean_ms=round(sum(timings) / len(timings), 4),
        p50_ms=round(percentile(timings, 0.50), 4),
        p95_ms=round(percentile(timings, 0.95), 4),
        p99_ms=round(percentile(timings, 0.99), 4),
        max_ms=round(max(timings), 4),
    )


def scale_records(
    *, services: int = SCALE_SERVICES, days: int = SCALE_DAYS, seed: int = 11
) -> list[dict]:
    """A year of daily spend for a larger estate — deterministic by seed."""
    records = []
    for index in range(services):
        scenario = build_scenario(
            f"scale-{index}",
            days=days,
            base=100.0 + index * 25,
            service=f"svc-{index:02d}",
            seed=seed + index,
            spikes=((days // 3, 4.0), (days - 7, 3.0)),
        )
        records.extend(scenario.records)
    return records


def reflex_latency_profile(
    records: list[dict] | None = None,
    *,
    label: str = "reflex",
    samples: int = 200,
) -> LatencyProfile:
    """Profile the reflex pass itself — the dual-loop design's fast lane.

    Imported lazily so the benchmark harness stays usable (and importable)
    without dragging the mission loader and the FastAPI layer behind it.
    """
    from app.detection import load_daily_costs
    from app.missions import get_mission
    from app.reflex import reflex_scan

    sample = records if records is not None else load_daily_costs()
    mission = get_mission()
    return measure(label, lambda: reflex_scan(sample, mission), samples=samples)
