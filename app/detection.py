"""Data loading and anomaly detection over cloud cost records.

Detection uses a z-score per record against its service's historical
mean; records at or above CRITICAL_Z_SCORE are critical, the rest are
warnings. The data source is synthetic (data/mock_costs.json); real
providers come in later sprints.
"""

import json
import statistics
from pathlib import Path

from app.models import Anomaly, DailyServiceSeries, ServiceCostSummary

DATA_FILE = Path(__file__).parent / "data" / "mock_costs.json"

# Flagged records at or above this |z-score| are critical; the rest are warnings.
CRITICAL_Z_SCORE = 3.0


def load_dataset() -> dict:
    with DATA_FILE.open() as f:
        return json.load(f)


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
    date_index = {date: i for i, date in enumerate(dates)}
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


def detect_anomalies(records: list, threshold: float) -> list[Anomaly]:
    """Flag records whose z-score against their service's history meets the threshold."""
    by_service = {}
    for record in records:
        by_service.setdefault(record["service"], []).append(record)

    anomalies = []
    for service, service_records in by_service.items():
        costs = [r["cost"] for r in service_records]
        mean = statistics.mean(costs)
        stdev = statistics.pstdev(costs)
        if stdev == 0:
            continue
        for record in service_records:
            z_score = (record["cost"] - mean) / stdev
            if abs(z_score) >= threshold:
                anomalies.append(
                    Anomaly(
                        service=service,
                        date=record["date"],
                        cost=record["cost"],
                        service_mean=round(mean, 2),
                        z_score=round(z_score, 2),
                        severity="critical" if abs(z_score) >= CRITICAL_Z_SCORE else "warning",
                    )
                )

    anomalies.sort(key=lambda a: abs(a.z_score), reverse=True)
    return anomalies
