"""CloudSentinel API — anomaly detection over cloud cost data.

Sprint 1 scope: anomaly detection over daily cost records (z-score against
each service's historical mean), a per-service cost summary with CSV export,
a daily trend series, a liveness check, and a dashboard served at the root.
Models live in models.py, data loading and detection logic in detection.py.
"""

import csv
import io
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app import db
from detection import (
    build_daily_series,
    detect_anomalies,
    load_daily_costs,
    load_dataset,
    summarize_costs,
)
from models import AnomalyReport, CostSummaryReport, DailyCostReport, HealthStatus

STATIC_DIR = Path(__file__).parent / "static"

# Content-Security-Policy for the public dashboard. script-src is locked to
# 'self' (the dashboard has no inline scripts, eval, or event handlers), so a
# reflected/stored string can never execute as script. style-src keeps
# 'unsafe-inline' because the dashboard applies inline style attributes and
# loads Google Fonts; injected data is still HTML-escaped in app.js.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "object-src 'none'",
    ]
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), interest-cohort=()",
}

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Build the schema on every boot: the deploy target's disk is ephemeral."""
    db.init_db()
    yield


app = FastAPI(
    title="CloudSentinel API",
    description=(
        "Monitors cloud cost data, detects anomalies and reports them "
        "for operator review (human-in-the-loop)."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Attach hardening headers to every response (dashboard, static, API)."""
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


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


@app.get("/costs/daily")
def get_daily_costs() -> DailyCostReport:
    """Return aligned per-service daily cost series for trend visualisations."""
    dataset = load_dataset()
    series = build_daily_series(dataset["daily_costs"])
    return DailyCostReport(
        currency=dataset["currency"],
        period=dataset["period"],
        dates=series["dates"],
        services=series["services"],
        totals=series["totals"],
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
