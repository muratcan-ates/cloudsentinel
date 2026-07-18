"""Pulse — the end-to-end agent chain (Sprint 2, WP-7).

One POST runs the whole pipeline over the current dataset:

    detect -> persist signals -> Analyst -> [debate-lite] -> Recommender
           -> proposed inbox cards (HITL decides; nothing executes itself)

Every hop emits a tagged JSON log line ([SIGNAL] here, [ANALYST] /
[DEBATE] / [RECOMMENDER] / [HITL] in their own modules), so a single
mock spike can be followed end to end in the server output.

Quota discipline: events that already carry an analysis are not
re-analyzed, and the Recommender's reuse lane keeps one open card per
signal — re-running Pulse is idempotent and cheap.
"""

import json
import logging
import sqlite3

from fastapi import APIRouter, Depends, Query

from app import db
from app.analyst import analyze_event
from app.missions import MissionError, get_mission
from app.recommender import recommend_for_event
from app.detection import DEFAULT_THRESHOLD, load_daily_costs, run_detection
from app.models import PulseChainLink, PulseReport
from app.reflex import reflex_scan
from app.security import persist_signals, scan_security

logger = logging.getLogger("cloudsentinel.pulse")

router = APIRouter(tags=["agents"])


@router.post("/pulse")
def run_pulse(
    threshold: float | None = Query(
        None,
        gt=0,
        allow_inf_nan=False,
        description=(
            "Z-score threshold for the detection pass; omitted, the "
            "mission's detection threshold governs."
        ),
    ),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> PulseReport:
    """Run detect → analyze → recommend for every current signal."""
    records = load_daily_costs()
    # Reflex first: the deterministic pass carries the mission's settings
    # and its measured latency opens the tagged log chain.
    try:
        reflex = reflex_scan(records, get_mission(), threshold)
        run, mission_name, reflex_ms = reflex.run, reflex.mission, reflex.latency_ms
        threshold = reflex.threshold
    except MissionError:
        logger.warning(
            "mission config unavailable; pulsing with environment defaults",
            exc_info=True,
        )
        threshold = threshold if threshold is not None else DEFAULT_THRESHOLD
        run, mission_name, reflex_ms = run_detection(records, threshold), None, None
    anomalies = run.anomalies
    logger.info(
        "[REFLEX] %s",
        json.dumps(
            {
                "mission": mission_name,
                "latency_ms": reflex_ms,
                "signals": len(anomalies),
                "detector": run.detector,
            },
            sort_keys=True,
        ),
    )

    links: list[PulseChainLink] = []
    analyzed_count = 0
    filed_count = 0
    reused_count = 0

    for anomaly in anomalies:
        with db.writing(conn):
            event_id = db.upsert_event(
                conn,
                kind="cost_anomaly",
                service=anomaly.service,
                occurred_on=anomaly.date,
                payload_json=anomaly.model_dump_json(exclude={"id"}),
            )
        logger.info(
            "[SIGNAL] %s",
            json.dumps(
                {
                    "event_id": event_id,
                    "service": anomaly.service,
                    "date": anomaly.date,
                    "z_score": anomaly.z_score,
                    "severity": anomaly.severity,
                },
                sort_keys=True,
            ),
        )

        event = conn.execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        if event["analysis_json"]:
            envelope = json.loads(event["analysis_json"])
            triage = envelope["report"]["triage"]
        else:
            analysis = analyze_event(conn, event)
            triage = analysis.triage
            analyzed_count += 1
            event = conn.execute(
                "SELECT * FROM events WHERE id = ?", (event_id,)
            ).fetchone()

        recommendation = recommend_for_event(conn, event)
        if recommendation.reused:
            reused_count += 1
        else:
            filed_count += 1

        links.append(
            PulseChainLink(
                event_id=event_id,
                service=anomaly.service,
                severity=anomaly.severity,
                triage=triage,
                action_id=recommendation.action_id,
                action_state=recommendation.action_state,
                preferred=recommendation.preferred,
                reused=recommendation.reused,
            )
        )

    # Unified detection: the security lane runs through the same line and
    # persists its own signals; it feeds no LLM agent (operator-facing).
    security_report = scan_security()
    persist_signals(conn, security_report.signals)

    return PulseReport(
        threshold=threshold,
        mission=mission_name,
        reflex_ms=reflex_ms,
        signals=len(links),
        security_signals=security_report.signal_count,
        analyzed=analyzed_count,
        proposals_filed=filed_count,
        proposals_reused=reused_count,
        chain=links,
    )
