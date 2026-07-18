"""CloudSentinel API — anomaly detection over cloud cost data.

Sprint 1 scope: anomaly detection over daily cost records (z-score against
each service's historical mean), a per-service cost summary with CSV export,
a daily trend series, a liveness check, and a dashboard served at the root.
All application code lives in the app/ package (models, detection, agents,
persistence); this module is the thin ASGI entry point.
"""

import csv
import io
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import sqlite3

from app import db
from app.actions import router as actions_router
from app.analyst import router as analyst_router
from app.analytics import metrics_router as metrics_router
from app.analytics import router as analytics_router
from app.decisions import router as decisions_router
from app.missions import MissionError, get_mission
from app.pulse import router as pulse_router
from app.recommender import router as recommender_router
from app.reflex import reflex_scan
from app.reflex import router as reflex_router
from app.detection import (
    DEFAULT_THRESHOLD,
    build_daily_series,
    load_daily_costs,
    load_dataset,
    run_detection,
    summarize_costs,
)
from app.models import AnomalyReport, CostSummaryReport, DailyCostReport, HealthStatus

STATIC_DIR = Path(__file__).parent / "static"

# The tagged agent log stream ([SIGNAL]/[ANALYST]/[DEBATE]/[RECOMMENDER]/
# [HITL]) rides the cloudsentinel.* loggers at INFO. Neither the root
# logger (WARNING, no handler) nor uvicorn's config (uvicorn.* only) would
# ever emit it, so the hierarchy gets its own stdout handler — exactly
# once, because --reload re-imports this module.
_agent_stream = logging.getLogger("cloudsentinel")
if not _agent_stream.handlers:
    _agent_handler = logging.StreamHandler()
    _agent_handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    _agent_stream.addHandler(_agent_handler)
    _agent_stream.setLevel(logging.INFO)

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
# Swagger UI and ReDoc load their bundles from cdn.jsdelivr.net and boot via
# an inline script, so the dashboard policy above renders them as a blank
# page. The API docs get a scoped policy instead; every other path keeps
# script-src 'self'.
DOCS_CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "font-src 'self'",
        "img-src 'self' data: https://fastapi.tiangolo.com",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "object-src 'none'",
    ]
)
DOCS_PATHS = {"/docs", "/redoc"}

SECURITY_HEADERS = {
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


app.include_router(actions_router)
app.include_router(analyst_router)
app.include_router(analytics_router)
app.include_router(decisions_router)
app.include_router(metrics_router)
app.include_router(reflex_router)
app.include_router(pulse_router)
app.include_router(recommender_router)

# Explicit origins and headers by locked decision: allow_credentials=True
# together with a wildcard origin is rejected by browsers, and the HITL
# POSTs need the Idempotency-Key header to survive preflight.
ALLOWED_ORIGINS = [
    "https://cloudsentinel.onrender.com",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Idempotency-Key", "Authorization", "Content-Type"],
    expose_headers=["Content-Disposition"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Attach hardening headers to every response (dashboard, static, API)."""
    response = await call_next(request)
    response.headers.setdefault(
        "Content-Security-Policy",
        DOCS_CONTENT_SECURITY_POLICY
        if request.url.path in DOCS_PATHS
        else CONTENT_SECURITY_POLICY,
    )
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
    threshold: float | None = Query(
        None,
        gt=0,
        allow_inf_nan=False,
        description=(
            "Z-score threshold at which a daily cost record is flagged; "
            "omitted, the mission's detection threshold governs."
        ),
    ),
    service: str | None = Query(
        None,
        min_length=1,
        description="If set, only return anomalies for this service (case-insensitive).",
    ),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> AnomalyReport:
    """Return cost records that deviate anomalously from their service's baseline.

    Detection scores each service's rolling baseline window (see
    app/detection.py for the window, detector and seasonality controls);
    the optional `service` filter only narrows what's returned. Every
    detected anomaly is persisted as an event with a stable id (upsert
    by natural key), which `POST /anomalies/{id}/analyze` targets —
    request-triggered scanning is the deployment model, so the scan is
    also the ingestion point.
    """
    records = load_daily_costs()
    # The reflex engine resolves the mission's detection settings and
    # measures the pass; if the mission config is unloadable the scan must
    # still answer (demo-critical endpoint), just without the mission tags.
    try:
        reflex = reflex_scan(records, get_mission(), threshold)
        run, mission_name, reflex_ms = reflex.run, reflex.mission, reflex.latency_ms
        threshold = reflex.threshold
    except MissionError:
        logging.getLogger("cloudsentinel.reflex").warning(
            "mission config unavailable; scanning with environment defaults",
            exc_info=True,
        )
        threshold = threshold if threshold is not None else DEFAULT_THRESHOLD
        run, mission_name, reflex_ms = run_detection(records, threshold), None, None
    anomalies = run.anomalies
    if anomalies:
        with db.writing(conn):
            for anomaly in anomalies:
                anomaly.id = db.upsert_event(
                    conn,
                    kind="cost_anomaly",
                    service=anomaly.service,
                    occurred_on=anomaly.date,
                    payload_json=anomaly.model_dump_json(exclude={"id"}),
                )
    service_filter = service.strip().lower() if service else None
    if service_filter:
        anomalies = [a for a in anomalies if a.service.lower() == service_filter]
    return AnomalyReport(
        threshold=threshold,
        records_analyzed=len(records),
        anomaly_count=len(anomalies),
        detector=run.detector,
        window_days=run.window_days,
        insufficient_data_services=run.insufficient_data_services,
        mission=mission_name,
        reflex_ms=reflex_ms,
        anomalies=anomalies,
    )


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    """Serve the CloudSentinel dashboard."""
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
