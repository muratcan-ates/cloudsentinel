"""Unified detection over security events (Sprint 3, S3-③).

Second signal source, SAME detection line: the security feed rides the
identical rolling-baseline / detector / reflex machinery the cost lane
uses. The adapter maps daily event counts into the detection layer's
value slot (``cost`` is that layer's value *key*, by history rather than
semantics — the arithmetic is value-agnostic).

Security signals persist as their own event kind and are NEVER routed
into the cost agents: the Analyst and Recommender stay cost-scoped (both
already 409 on foreign kinds), so a security signal is an operator-facing
fact, not an LLM conversation.
"""

import json
import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from app import db
from app.detection import DEFAULT_THRESHOLD, shift_iso, demo_rebase_delta, run_detection
from app.missions import MissionError, get_mission
from app.models import SecuritySignal, SecuritySignalReport
from app.reflex import reflex_scan

logger = logging.getLogger("cloudsentinel.security")

router = APIRouter(prefix="/security", tags=["security"])

SECURITY_DATA_FILE = Path(__file__).parent / "data" / "mock_security_events.json"

EVENT_KIND = "security_anomaly"


def load_security_dataset() -> dict:
    with SECURITY_DATA_FILE.open() as f:
        dataset = json.load(f)
    # Same whole-week demo shift as the cost lane, so cross-lane same-day
    # correlations (a login storm on a spend-spike day) survive the rebase.
    delta = demo_rebase_delta()
    if delta:
        for row in dataset["daily_counts"]:
            row["date"] = shift_iso(row["date"], delta)
    return dataset


def security_records(dataset: dict) -> list[dict]:
    """Adapt daily counts into the detection layer's record shape."""
    return [
        {
            "service": row["service"],
            "date": row["date"],
            "cost": float(row["count"]),
        }
        for row in dataset["daily_counts"]
    ]


def persist_signals(conn: sqlite3.Connection, signals: list[SecuritySignal]) -> None:
    """Upsert each signal as a security event with a stable natural-key id.

    Emits the same [SIGNAL] tagged log line the cost lane uses, with the
    kind field carrying the lane.
    """
    if not signals:
        return
    with db.writing(conn):
        for signal in signals:
            signal.id = db.upsert_event(
                conn,
                kind=EVENT_KIND,
                service=signal.service,
                occurred_on=signal.date,
                payload_json=signal.model_dump_json(exclude={"id"}),
            )
    for signal in signals:
        logger.info(
            "[SIGNAL] %s",
            json.dumps(
                {
                    "kind": EVENT_KIND,
                    "event_id": signal.id,
                    "service": signal.service,
                    "date": signal.date,
                    "count": signal.count,
                    "z_score": signal.z_score,
                    "severity": signal.severity,
                },
                sort_keys=True,
            ),
        )


def scan_security(threshold: float | None = None) -> SecuritySignalReport:
    """Run the security mission's detection pass over the mock feed."""
    dataset = load_security_dataset()
    records = security_records(dataset)
    try:
        reflex = reflex_scan(records, get_mission("security"), threshold)
        run, mission_name, reflex_ms = reflex.run, reflex.mission, reflex.latency_ms
        resolved = reflex.threshold
    except MissionError:
        logger.warning(
            "security mission unavailable; scanning with environment defaults",
            exc_info=True,
        )
        resolved = threshold if threshold is not None else DEFAULT_THRESHOLD
        run, mission_name, reflex_ms = run_detection(records, resolved), None, None

    signals = [
        SecuritySignal(
            service=anomaly.service,
            date=anomaly.date,
            count=anomaly.cost,
            baseline=anomaly.service_mean,
            z_score=anomaly.z_score,
            severity=anomaly.severity,
            detector=anomaly.detector,
        )
        for anomaly in run.anomalies
    ]
    return SecuritySignalReport(
        metric=dataset["metric"],
        threshold=resolved,
        mission=mission_name,
        reflex_ms=reflex_ms,
        window_days=run.window_days,
        signal_count=len(signals),
        insufficient_data_services=run.insufficient_data_services,
        signals=signals,
    )


@router.get("/signals")
def get_security_signals(
    threshold: float | None = Query(
        None,
        gt=0,
        allow_inf_nan=False,
        description=(
            "Z-score threshold for the security pass; omitted, the security "
            "mission's detection threshold governs."
        ),
    ),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> SecuritySignalReport:
    """Scan the security feed through the unified detection line.

    Like the cost scan, this is also the ingestion point: every flagged
    signal is persisted with a stable event id (request-triggered
    deployment model).
    """
    report = scan_security(threshold)
    persist_signals(conn, report.signals)
    return report
