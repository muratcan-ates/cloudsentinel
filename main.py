"""CloudSentinel API — anomaly detection over cloud cost data.

Sprint 1 scope: a single endpoint that flags daily cost records deviating
from their service's historical mean, using a z-score threshold. The data
source is synthetic (data/mock_costs.json); real providers come in later
sprints.
"""

import json
import statistics
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query
from pydantic import BaseModel

DATA_FILE = Path(__file__).parent / "data" / "mock_costs.json"

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


def load_daily_costs() -> list:
    with DATA_FILE.open() as f:
        return json.load(f)["daily_costs"]


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
