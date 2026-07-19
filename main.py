"""CloudSentinel API — agent-assisted anomaly watch over cloud cost,
security and fraud signals.

A deterministic detection core (rolling baseline · z-score / MAD · weekly
seasonality) feeds a visible-reasoning agent chain — analyst triage,
recommender options with Python-computed savings, a debate-lite skeptic and
a chronicler briefing — over a Gemini (deterministic fake by default)
provider. Critical decisions stay with a human operator (human-in-the-loop);
verdicts persist as decision memory and surface through the
operations-intelligence analytics. All application code lives in the app/
package (detection, missions/reflex, the agents, persistence, analytics);
this module is the thin ASGI entry point that wires the routers, the security
headers and the dashboard.
"""

import csv
import io
import json
import logging
import os
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import sqlite3

from app import db
from app.actions import router as actions_router
from app.analyst import router as analyst_router
from app.auth import router as auth_router
from app.analytics import metrics_router as metrics_router
from app.analytics import router as analytics_router
from app.bus import router as bus_router
from app.decisions import router as decisions_router
from app.insights import router as insights_router
from app.llm import provider_mode
from app.missions import MissionError, get_mission
from app.ops import router as ops_router
from app.pulse import router as pulse_router
from app.recommender import router as recommender_router
from app.fraud import router as fraud_router
from app.reflex import reflex_scan
from app.reflex import router as reflex_router
from app.routines import router as routines_router
from app.runbooks import router as runbooks_router
from app.security import router as security_router
from app.detection import (
    DEFAULT_THRESHOLD,
    build_daily_series,
    load_daily_costs,
    load_dataset,
    run_detection,
    summarize_costs,
)
from app.models import (
    AnomalyReport,
    CostSummaryReport,
    DailyCostReport,
    HealthStatus,
    ReadinessCheck,
    ReadinessStatus,
)

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
# reflected/stored string can never execute as script. Fonts are now
# self-hosted (static/fonts/), so default-src 'self' covers them and no
# external host is allowed anywhere. style-src keeps 'unsafe-inline' only
# for the dashboard's inline style attributes; injected data is still
# HTML-escaped in app.js.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "font-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "object-src 'none'",
    ]
)
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
    _log_boot_manifest()
    yield


def _log_boot_manifest() -> None:
    """One [BOOT] line naming what this instance is running — the first
    frame of the demo and a deploy sanity check in one glance."""
    try:
        dataset = load_dataset()
        services = sorted({r["service"] for r in dataset["daily_costs"]})
        span = f"{dataset['period']['start']}→{dataset['period']['end']}"
    except Exception:  # a broken dataset must not block boot
        services, span = [], "unknown"
    logging.getLogger("cloudsentinel").info(
        "[BOOT] %s",
        json.dumps(
            {
                "version": app.version,
                "env": os.environ.get("SENTINEL_ENV", "local"),
                "provider": provider_mode(),
                "readonly": readonly_enabled(),
                "services": services,
                "period": span,
            },
            sort_keys=True,
        ),
    )


# FastAPI's built-in docs pages boot Swagger UI from cdn.jsdelivr.net via an
# inline script — both blocked by the strict CSP. The bundle is vendored
# instead (static/vendor/, swagger-ui-dist 5.32.9) and served from a static
# page whose boot script is an external file, so /docs runs under the same
# script-src 'self' policy as the dashboard. ReDoc is dropped rather than
# vendored: one API browser is product, two is surface area.
app = FastAPI(
    title="CloudSentinel API",
    description=(
        "Agent-assisted anomaly watch over cloud cost, security and fraud "
        "signals: a deterministic detection core feeds a visible-reasoning "
        "agent chain (analyst, recommender, debate-lite skeptic, chronicler); "
        "critical actions stay with a human operator (human-in-the-loop) and "
        "persist as decision memory with operations-intelligence analytics."
    ),
    version="0.3.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)


app.include_router(actions_router)
app.include_router(analyst_router)
app.include_router(auth_router)
app.include_router(bus_router)
app.include_router(analytics_router)
app.include_router(decisions_router)
app.include_router(fraud_router)
app.include_router(insights_router)
app.include_router(metrics_router)
app.include_router(ops_router)
app.include_router(reflex_router)
app.include_router(routines_router)
app.include_router(runbooks_router)
app.include_router(security_router)
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


# Hand-rolled sliding-window limiter for the expensive pulse chain (stdlib
# by locked decision — no rate-limit dependency in the competition window).
# Per-client-IP, minute window; 0 disables.
RATE_LIMIT_ENV = "SENTINEL_PULSE_RATE_LIMIT_PER_MINUTE"
DEFAULT_PULSE_RATE_LIMIT = 60
RATE_WINDOW_SECONDS = 60.0
_pulse_hits: defaultdict[str, deque] = defaultdict(deque)

# Read-only showcase mode: a public demo link must survive strangers'
# clicks. One env knob blocks every write while the panels keep reading.
READONLY_ENV = "SENTINEL_READONLY"


def readonly_enabled() -> bool:
    return os.environ.get(READONLY_ENV, "").strip() == "1"


def pulse_rate_limit() -> int:
    raw = os.environ.get(RATE_LIMIT_ENV, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_PULSE_RATE_LIMIT
    return value if value >= 0 else DEFAULT_PULSE_RATE_LIMIT


@app.middleware("http")
async def guard_expensive_endpoints(request: Request, call_next):
    """Rate-limit POST /pulse and tag every response with a request id."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and readonly_enabled():
        return Response(
            content='{"detail": "read-only demo mode — write operations are disabled"}',
            status_code=403,
            media_type="application/json",
            headers={"X-Request-ID": request_id},
        )
    if request.method == "POST" and request.url.path == "/pulse":
        limit = pulse_rate_limit()
        if limit > 0:
            client = request.client.host if request.client else "unknown"
            now = time.monotonic()
            hits = _pulse_hits[client]
            while hits and now - hits[0] > RATE_WINDOW_SECONDS:
                hits.popleft()
            if len(hits) >= limit:
                return Response(
                    content='{"detail": "pulse rate limit exceeded"}',
                    status_code=429,
                    media_type="application/json",
                    headers={"Retry-After": "60", "X-Request-ID": request_id},
                )
            hits.append(now)
    response = await call_next(request)
    response.headers.setdefault("X-Request-ID", request_id)
    return response


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Attach hardening headers to every response (dashboard, static, API)."""
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
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
    return HealthStatus(
        status="ok",
        env=os.environ.get("SENTINEL_ENV", "local"),
        version=app.version,
        provider=provider_mode(),
        readonly=readonly_enabled(),
    )


@app.get("/ready")
def readiness_check(response: Response) -> ReadinessStatus:
    """Readiness probe: verify the dependencies a real request needs.

    /health answers as long as the process is up; /ready goes one step
    further and confirms the database is reachable, the mission config
    parses and the dataset loads — so a deploy or uptime monitor can gate
    traffic on genuine readiness. Answers 503 if any check fails.
    """
    checks: list[ReadinessCheck] = []

    try:
        # Match the API's lazy self-init (get_db): an ASGI runner that defers
        # the lifespan must not let /ready report 503 while the endpoints
        # already serve fine by building the schema on first use. init_db is
        # idempotent, so this is a no-op once the schema exists.
        db.init_db(db.db_path())
        conn = db.connect()
        try:
            conn.execute("SELECT count(*) FROM events").fetchone()
        finally:
            conn.close()
        checks.append(ReadinessCheck(name="database", ok=True, detail="reachable"))
    except Exception as exc:  # connectivity or missing schema
        checks.append(ReadinessCheck(name="database", ok=False, detail=str(exc)[:120]))

    try:
        mission_name = get_mission().mission
        checks.append(
            ReadinessCheck(name="missions", ok=True, detail=f"loaded ({mission_name})")
        )
    except MissionError as exc:
        checks.append(ReadinessCheck(name="missions", ok=False, detail=str(exc)[:120]))

    try:
        rows = len(load_dataset()["daily_costs"])
        checks.append(ReadinessCheck(name="dataset", ok=True, detail=f"{rows} cost rows"))
    except Exception as exc:
        checks.append(ReadinessCheck(name="dataset", ok=False, detail=str(exc)[:120]))

    ready = all(check.ok for check in checks)
    if not ready:
        response.status_code = 503
    return ReadinessStatus(
        ready=ready,
        version=app.version,
        provider=provider_mode(),
        checks=checks,
    )


# Failure envelope: an operator (or a jury member) probing the API gets a
# JSON answer, never a traceback. The X-Request-ID middleware cannot wrap
# these (unhandled errors propagate past it), so they stay minimal.
@app.exception_handler(sqlite3.OperationalError)
async def database_busy(request: Request, exc: sqlite3.OperationalError) -> JSONResponse:
    """A write contended beyond the busy timeout answers 503, not a 500."""
    logging.getLogger("cloudsentinel.error").warning(
        "database busy on %s: %s", request.url.path, exc
    )
    return JSONResponse(
        {"detail": "database is busy — retry shortly"},
        status_code=503,
        headers={"Retry-After": "2"},
    )


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort envelope; the traceback goes to the log, not the wire."""
    logging.getLogger("cloudsentinel.error").exception(
        "unhandled error on %s", request.url.path
    )
    return JSONResponse({"detail": "internal server error"}, status_code=500)


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
def export_cost_summary_csv(
    schema: Literal["default", "focus"] = Query(
        "default",
        description=(
            "CSV column schema: 'focus' maps the summary onto FinOps FOCUS "
            "1.4 column names for multi-cloud interoperability."
        ),
    ),
) -> Response:
    """Return the per-service cost summary as a downloadable CSV file."""
    dataset = load_dataset()
    records = dataset["daily_costs"]
    services = summarize_costs(records)

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    if schema == "focus":
        # FinOps FOCUS 1.4 field names: the industry-standard shape for
        # normalized multi-cloud cost data (interoperability by naming).
        writer.writerow(
            [
                "ServiceName",
                "BilledCost",
                "BillingCurrency",
                "ChargePeriodStart",
                "ChargePeriodEnd",
            ]
        )
        for service in services:
            writer.writerow(
                [
                    service.service,
                    service.total_cost,
                    dataset["currency"],
                    dataset["period"]["start"],
                    dataset["period"]["end"],
                ]
            )
        filename = "cost_summary_focus.csv"
    else:
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
        filename = "cost_summary.csv"

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
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
    leave_one_out: bool | None = Query(
        None,
        description=(
            "Exclude each record from its own baseline (contamination-resistant "
            "scoring); omitted, the SENTINEL_LEAVE_ONE_OUT default governs."
        ),
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
        reflex = reflex_scan(
            records, get_mission(), threshold, leave_one_out=leave_one_out
        )
        run, mission_name, reflex_ms = reflex.run, reflex.mission, reflex.latency_ms
        threshold = reflex.threshold
    except MissionError:
        logging.getLogger("cloudsentinel.reflex").warning(
            "mission config unavailable; scanning with environment defaults",
            exc_info=True,
        )
        threshold = threshold if threshold is not None else DEFAULT_THRESHOLD
        run, mission_name, reflex_ms = (
            run_detection(records, threshold, leave_one_out=leave_one_out),
            None,
            None,
        )
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


# The dashboard's rooms are real URLs (back/forward and sharing work); the
# client resolves the view from the path, so every room serves the same page.
for _view_path in ("/watch", "/investigate", "/decide", "/intel", "/brain", "/broadsheet"):
    app.add_api_route(_view_path, dashboard, include_in_schema=False)


@app.get("/docs", include_in_schema=False)
def api_docs() -> FileResponse:
    """Serve the self-hosted Swagger UI (no CDN, same CSP as everything)."""
    return FileResponse(STATIC_DIR / "docs.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
