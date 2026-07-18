"""Data loading and anomaly detection over cloud cost records.

Detection scores each record inside a rolling baseline window against its
service's recent history and flags records whose deviation meets the
threshold; records at or above CRITICAL_Z_SCORE are critical, the rest
are warnings. The data source is synthetic (data/mock_costs.json); real
providers come in later sprints.

Detection-quality controls (Sprint 3, pure Python by locked decision):

- Rolling baseline: statistics come from a true calendar window — the
  ``SENTINEL_BASELINE_WINDOW_DAYS`` days (default 28) ending at the
  dataset's newest date — not from each service's whole history. A
  months-old regime cannot poison today's baseline, only records inside
  the window are scored, and a service whose data stopped before the
  window ages out into the insufficient-data list instead of being
  scored against fossils.
- Insufficient history: services with fewer than ``MIN_HISTORY`` records
  in the window are excluded from flagging and reported separately —
  two data points are not a baseline.
- ``SENTINEL_DETECTOR=mad`` switches the baseline to median + scaled
  median-absolute-deviation, which a single extreme spike cannot poison
  the way it inflates a mean/stdev. A flat-median series (MAD = 0)
  falls back to the classic z-score so real spikes are still caught.
- ``SENTINEL_SEASONAL=1`` opts into day-of-week baselines (Mondays are
  compared with Mondays) whenever every weekday bucket in the window
  holds at least ``MIN_WEEKDAY_SAMPLES`` records AND is large enough to
  matter statistically: with a self-inclusive population stdev the
  largest attainable |z| in an n-sample group is sqrt(n-1), so a bucket
  that cannot mathematically reach the requested threshold would
  silently disable detection — those services fall back to the flat
  baseline instead.
- Every flagged anomaly records which detector and parameters produced
  it (``detector`` / ``detector_params``) so "why was this flagged" has
  a durable answer in the event payload.
"""

import json
import os
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from app.models import Anomaly, DailyServiceSeries, ServiceCostSummary

DATA_FILE = Path(__file__).parent / "data" / "mock_costs.json"

# Flagged records at or above this |z-score| are critical; the rest are
# warnings. Missions may override per scan via run_detection(critical_z=...).
CRITICAL_Z_SCORE = 3.0
DEFAULT_THRESHOLD = 2.0

DETECTOR_ENV = "SENTINEL_DETECTOR"  # zscore (default) | mad
WINDOW_ENV = "SENTINEL_BASELINE_WINDOW_DAYS"
SEASONAL_ENV = "SENTINEL_SEASONAL"  # "1"/"true" opts into day-of-week baselines
REBASE_ENV = "SENTINEL_REBASE_DATES"  # "1" shifts demo data toward today

DEFAULT_WINDOW_DAYS = 28
MIN_HISTORY = 7
MIN_WEEKDAY_SAMPLES = 3
MAD_SCALE = 1.4826  # normal-consistency constant: scaled MAD estimates sigma


def detector_mode() -> str:
    raw = os.environ.get(DETECTOR_ENV, "").strip().lower()
    return raw if raw in ("zscore", "mad") else "zscore"


def baseline_window_days() -> int:
    raw = os.environ.get(WINDOW_ENV, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_WINDOW_DAYS
    return value if value >= MIN_HISTORY else DEFAULT_WINDOW_DAYS


def seasonal_enabled() -> bool:
    # Accept the same truthy set as the mission path's parser (reflex.py
    # _env_seasonal) so SENTINEL_SEASONAL means the same thing whether a scan
    # runs through the mission or the degraded env-defaults fallback.
    return os.environ.get(SEASONAL_ENV, "").strip().lower() in ("1", "true")


def demo_rebase_delta() -> timedelta:
    """Whole-week shift landing the cost dataset's newest day near yesterday.

    Demo freshness (SENTINEL_REBASE_DATES=1): a jury should see a spike
    from "this week", not from a frozen fixture three weeks back. The
    shift is quantized to whole weeks so weekday alignment — and with it
    the seasonal baseline — survives, and EVERY mock dataset (cost,
    security, fraud) applies this same delta so cross-lane same-day
    correlations stay intact. Zero when the knob is off or the data is
    already current.
    """
    if os.environ.get(REBASE_ENV, "").strip() != "1":
        return timedelta(0)
    with DATA_FILE.open() as f:
        raw = json.load(f)
    newest = date.fromisoformat(max(r["date"] for r in raw["daily_costs"]))
    yesterday = date.today() - timedelta(days=1)
    if newest >= yesterday:
        return timedelta(0)
    return timedelta(days=((yesterday - newest).days // 7) * 7)


def shift_iso(value: str, delta: timedelta) -> str:
    return (date.fromisoformat(value) + delta).isoformat()


def load_dataset() -> dict:
    with DATA_FILE.open() as f:
        dataset = json.load(f)
    delta = demo_rebase_delta()
    if delta:
        for record in dataset["daily_costs"]:
            record["date"] = shift_iso(record["date"], delta)
        period = dataset.get("period")
        if period:
            period["start"] = shift_iso(period["start"], delta)
            period["end"] = shift_iso(period["end"], delta)
    return dataset


def load_daily_costs() -> list:
    return load_dataset()["daily_costs"]


def summarize_costs(records: list) -> list[ServiceCostSummary]:
    """Aggregate daily records into per-service cost summaries, biggest spender first."""
    by_service = {}
    for record in records:
        by_service.setdefault(record["service"], []).append(record["cost"])

    grand_total = sum(cost for costs in by_service.values() for cost in costs)

    summaries = [
        ServiceCostSummary(
            service=service,
            total_cost=round(sum(costs), 2),
            mean_daily_cost=round(statistics.mean(costs), 2),
            min_daily_cost=min(costs),
            max_daily_cost=max(costs),
            share_of_total=round(sum(costs) / grand_total, 4) if grand_total else 0.0,
        )
        for service, costs in by_service.items()
    ]
    summaries.sort(key=lambda s: s.total_cost, reverse=True)
    return summaries


def build_daily_series(records: list) -> dict:
    """Align daily records into per-service series over the sorted date range.

    Dates missing for a service contribute 0 so every series has the same
    length as the date axis; costs on the same service+date accumulate.
    """
    dates = sorted({record["date"] for record in records})
    date_index = {date_: i for i, date_ in enumerate(dates)}
    by_service = {}
    for record in records:
        values = by_service.setdefault(record["service"], [0.0] * len(dates))
        values[date_index[record["date"]]] += record["cost"]

    services = [
        DailyServiceSeries(service=service, values=[round(v, 2) for v in values])
        for service, values in sorted(by_service.items())
    ]
    # Totals derive from the published (rounded) values so the column-sum
    # invariant holds even for sub-cent inputs.
    totals = [
        round(sum(series.values[i] for series in services), 2)
        for i in range(len(dates))
    ]
    return {"dates": dates, "services": services, "totals": totals}


@dataclass
class _Baseline:
    center: float
    spread: float
    label: str  # which detector actually produced these statistics


def _baseline(costs: list[float], mode: str) -> _Baseline | None:
    """Build the comparison statistics for one group of costs.

    Returns None for a group with no measurable spread — a perfectly flat
    series carries no deviation signal under either detector.
    """
    if mode == "mad":
        median = statistics.median(costs)
        mad = statistics.median(abs(cost - median) for cost in costs)
        if mad > 0:
            return _Baseline(center=median, spread=MAD_SCALE * mad, label="mad")
        # Flat median with real outliers (over half the window identical):
        # scaled MAD collapses to zero and would hide every spike, so the
        # classic z-score takes over for this group, honestly labeled.
        mean = statistics.mean(costs)
        stdev = statistics.pstdev(costs)
        if stdev == 0:
            return None
        return _Baseline(center=mean, spread=stdev, label="mad->zscore")
    mean = statistics.mean(costs)
    stdev = statistics.pstdev(costs)
    if stdev == 0:
        return None
    return _Baseline(center=mean, spread=stdev, label="zscore")


def _weekday(record: dict) -> int | None:
    try:
        return date.fromisoformat(str(record["date"])).weekday()
    except ValueError:
        return None


def _window_cutoff(records: list, window_days: int) -> str | None:
    """First ISO date inside the calendar window ending at the newest record.

    Anchored to the dataset's newest date (not the wall clock) so mock
    and historical datasets window correctly. Returns None when any date
    is unparseable — callers then fall back to a record-count slice.
    """
    try:
        newest = max(date.fromisoformat(str(record["date"])) for record in records)
    except ValueError:
        return None
    return (newest - timedelta(days=window_days - 1)).isoformat()


@dataclass
class DetectionRun:
    """Everything one detection pass produced, registry included."""

    anomalies: list[Anomaly]
    insufficient_data_services: list[str]
    detector: str
    window_days: int
    seasonal: bool


def run_detection(
    records: list,
    threshold: float,
    *,
    detector: str | None = None,
    window: int | None = None,
    seasonal: bool | None = None,
    critical_z: float | None = None,
) -> DetectionRun:
    """Score each service's recent records against its rolling baseline.

    Keyword overrides exist for the reflex engine, tests and the benchmark
    harness; called bare, the knobs resolve from the environment.
    """
    mode = detector if detector in ("zscore", "mad") else detector_mode()
    window_days = window if window and window >= MIN_HISTORY else baseline_window_days()
    use_seasonal = seasonal_enabled() if seasonal is None else seasonal
    critical_cutoff = (
        critical_z if critical_z is not None and critical_z > 0 else CRITICAL_Z_SCORE
    )

    by_service: dict[str, list[dict]] = {}
    for record in records:
        by_service.setdefault(record["service"], []).append(record)
    cutoff = _window_cutoff(records, window_days) if records else None

    anomalies: list[Anomaly] = []
    insufficient: list[str] = []
    for service, service_records in by_service.items():
        service_records.sort(key=lambda record: str(record["date"]))
        if cutoff is not None:
            # True calendar window: a service whose data stopped before the
            # window holds nothing here and ages out via the insufficient
            # list instead of being scored against fossil records.
            windowed = [
                record for record in service_records if str(record["date"]) >= cutoff
            ]
        else:
            windowed = service_records[-window_days:]
        if len(windowed) < MIN_HISTORY:
            insufficient.append(service)
            continue

        # Day-of-week baselines only when every weekday bucket in the window
        # is sampled well enough to be a baseline of its own — statistically,
        # not just by count: with a self-inclusive pstdev the largest
        # attainable |z| in an n-sample group is sqrt(n-1), so a bucket with
        # n - 1 <= threshold^2 could never flag anything and would silently
        # disable detection. Such services keep their flat baseline.
        groups: list[list[dict]] = [windowed]
        seasonal_applied = False
        if use_seasonal:
            buckets: dict[int, list[dict]] = {}
            for record in windowed:
                weekday = _weekday(record)
                if weekday is None:
                    buckets = {}
                    break
                buckets.setdefault(weekday, []).append(record)
            if buckets and all(
                len(bucket) >= MIN_WEEKDAY_SAMPLES
                and len(bucket) - 1 > threshold * threshold
                for bucket in buckets.values()
            ):
                groups = list(buckets.values())
                seasonal_applied = True

        for group in groups:
            baseline = _baseline([record["cost"] for record in group], mode)
            if baseline is None:
                continue
            label = baseline.label + ("+weekday" if seasonal_applied else "")
            for record in group:
                # The published (rounded) score decides flagging and severity,
                # so a reader recomputing from the payload always agrees with
                # the stored severity — no raw-vs-rounded disagreement band.
                score = round(
                    (record["cost"] - baseline.center) / baseline.spread, 2
                )
                if abs(score) >= threshold:
                    anomalies.append(
                        Anomaly(
                            service=service,
                            date=record["date"],
                            cost=record["cost"],
                            service_mean=round(baseline.center, 2),
                            z_score=score,
                            severity=(
                                "critical"
                                if abs(score) >= critical_cutoff
                                else "warning"
                            ),
                            detector=label,
                            # Persisted into the event payload on purpose: a
                            # config change re-keys LLM caches, and that is
                            # correct — a different detector asks a different
                            # question about the same numbers.
                            detector_params={
                                "window_days": window_days,
                                "min_history": MIN_HISTORY,
                                "seasonal": seasonal_applied,
                            },
                        )
                    )

    anomalies.sort(key=lambda a: abs(a.z_score), reverse=True)
    insufficient.sort()
    return DetectionRun(
        anomalies=anomalies,
        insufficient_data_services=insufficient,
        detector=mode,
        window_days=window_days,
        seasonal=use_seasonal,
    )
