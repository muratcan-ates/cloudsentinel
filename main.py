"""CloudSentinel API — anomaly detection over cloud cost data.

Sprint 1 scope: anomaly detection over daily cost records (z-score against
each service's historical mean), a per-service cost summary with CSV export,
a liveness check, and a dashboard served at the root. Models live in
models.py, data loading and detection logic in detection.py.
"""

import csv
import io
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from detection import (
    detect_anomalies,
    load_daily_costs,
    load_dataset,
    summarize_costs,
)
from models import AnomalyReport, CostSummaryReport, HealthStatus

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="CloudSentinel API",
    description=(
        "Monitors cloud cost data, detects anomalies and reports them "
        "for operator review (human-in-the-loop)."
    ),
    version="0.1.0",
)


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


@app.get("/health")
def health_check() -> HealthStatus:
    """Simple liveness check for monitoring/deployment."""
    return HealthStatus(status="ok")


@app.get(
    "/costs/summary/export",
    response_class=Response,
    responses={
        200: {
            "content": {"text/csv": {}},
            "description": "CSV download of the per-service cost summary.",
        }
    },
)
def export_cost_summary_csv() -> Response:
    """Return the per-service cost summary as a downloadable CSV file."""
    records = load_daily_costs()
    services = summarize_costs(records)

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    writer.writerow(
        [
            "service",
            "total_cost",
            "mean_daily_cost",
            "min_daily_cost",
            "max_daily_cost",
            "share_of_total",
        ]
    )

    for service in services:
        writer.writerow(
            [
                service.service,
                service.total_cost,
                service.mean_daily_cost,
                service.min_daily_cost,
                service.max_daily_cost,
                service.share_of_total,
            ]
        )

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=cost_summary.csv"
        },
    )


@app.get("/anomalies")
def get_anomalies(
    threshold: float = Query(
        2.0,
        gt=0,
        allow_inf_nan=False,
        description="Z-score threshold at which a daily cost record is flagged.",
    ),
    service: str | None = Query(
        None,
        min_length=1,
        description="If set, only return anomalies for this service (case-insensitive).",
    ),
) -> AnomalyReport:
    """Return cost records that deviate anomalously from their service's mean.

    Anomaly detection always runs over the full dataset so that each
    service's mean/stdev is computed from its complete history; the
    optional `service` filter only narrows what's returned.
    """
    records = load_daily_costs()
    anomalies = detect_anomalies(records, threshold)
    service_filter = service.strip().lower() if service else None
    if service_filter:
        anomalies = [a for a in anomalies if a.service.lower() == service_filter]
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
