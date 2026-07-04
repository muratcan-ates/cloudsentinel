"""CloudSentinel API — anomaly detection over cloud cost data.

Sprint 1 scope: anomaly detection over daily cost records (z-score against
each service's historical mean), a per-service cost summary, and a dashboard
served at the root. The data source is synthetic (data/mock_costs.json);
real providers come in later sprints.
"""

import json
import statistics
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DATA_FILE = Path(__file__).parent / "data" / "mock_costs.json"
STATIC_DIR = Path(__file__).parent / "static"

# Flagged records at or above this |z-score| are critical; the rest are warnings.
CRITICAL_Z_SCORE = 3.0

app = FastAPI(
    title="CloudSentinel API",
    description=(
        "Monitors cloud cost data, detects anomalies and reports them "
        "for operator review (human-in-the-loop)."
    ),
    version="0.1.0",
)


class ServiceCostSummary(BaseModel):
    service: str
    total_cost: float
    mean_daily_cost: float
    min_daily_cost: float
    max_daily_cost: float
    share_of_total: float


class Period(BaseModel):
    start: str
    end: str


class CostSummaryReport(BaseModel):
    currency: str
    period: Period
    records_analyzed: int
    total_cost: float
    services: list[ServiceCostSummary]


class Anomaly(BaseModel):
    service: str
    date: str
    cost: float
    service_mean: float
    z_score: float
    severity: Literal["critical", "warning"]


class AnomalyReport(BaseModel):
    threshold: float
    records_analyzed: int
    anomaly_count: int
    anomalies: list[Anomaly]


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


@app.get("/costs/summary")
def get_cost_summary() -> CostSummaryReport:
    """Return per-service cost totals and their share of overall spend."""
    dataset = load_dataset()
    records = dataset["daily_costs"]
    services = summarize_costs(records)
    return CostSummaryReport(
        currency=dataset["currency"],
        period=dataset["period"],
        records_analyzed=len(records),
        total_cost=round(sum(s.total_cost for s in services), 2),
        services=services,
    )


@app.get("/anomalies")
def get_anomalies(
    threshold: float = Query(
        2.0,
        gt=0,
        allow_inf_nan=False,
        description="Z-score threshold at which a daily cost record is flagged.",
    ),
) -> AnomalyReport:
    """Return cost records that deviate anomalously from their service's mean."""
    records = load_daily_costs()
    anomalies = detect_anomalies(records, threshold)
    return AnomalyReport(
        threshold=threshold,
        records_analyzed=len(records),
        anomaly_count=len(anomalies),
        anomalies=anomalies,
    )


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    """Serve the CloudSentinel dashboard."""
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
