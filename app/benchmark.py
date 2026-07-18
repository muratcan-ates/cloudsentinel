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

import random
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
    spikes: tuple[tuple[int, float], ...] = (),
    service: str = "svc",
    seed: int = 7,
    start: date = date(2026, 6, 1),
) -> Scenario:
    """One service's synthetic daily series with planted spikes.

    ``spikes`` are (day_index, multiplier) pairs; the record at that index
    is overwritten with ``base * multiplier`` and becomes ground truth.
    """
    rng = random.Random(seed)
    records = []
    for index in range(days):
        day = start + timedelta(days=index)
        cost = base + (rng.uniform(-noise, noise) if noise else 0.0)
        if weekend_uplift and day.weekday() >= 5:
            cost += weekend_uplift
        records.append(
            {"service": service, "date": day.isoformat(), "cost": round(cost, 2)}
        )
    planted = set()
    for index, multiplier in spikes:
        records[index]["cost"] = round(base * multiplier, 2)
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
) -> BenchmarkResult:
    """Score one detector configuration against a scenario's ground truth."""
    run = run_detection(
        scenario.records,
        threshold,
        detector=detector,
        seasonal=seasonal,
        window=window or len(scenario.records),
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
    """The three claims the detection layer makes, as testable scenarios."""
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
    ]
