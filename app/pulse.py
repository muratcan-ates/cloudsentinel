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
import os
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from app import db, fraud
from app.analyst import analyze_event
from app.chronicler import write_briefing
from app.missions import MissionError, get_mission
from app.recommender import recommend_for_event
from app.detection import DEFAULT_THRESHOLD, load_daily_costs, run_detection
from app.models import LastPulseReport, PulseBriefing, PulseChainLink, PulseReport
from app.llm import llm_call_budget
from app.reflex import reflex_scan
from app.security import persist_signals, scan_security

logger = logging.getLogger("cloudsentinel.pulse")

router = APIRouter(tags=["agents"])

# One pulse may spend at most this many provider calls (S3-⑤). The mock
# demo needs ~6 (2 signals x analyst+reflection+recommender); debates can
# add two more and the chronicler's briefing is one — 10 covers the full
# chain without letting a runaway loop burn the day's free-tier quota.
PULSE_BUDGET_ENV = "SENTINEL_PULSE_LLM_BUDGET"
DEFAULT_PULSE_LLM_BUDGET = 10


def pulse_llm_budget() -> int:
    raw = os.environ.get(PULSE_BUDGET_ENV, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_PULSE_LLM_BUDGET
    return value if value >= 0 else DEFAULT_PULSE_LLM_BUDGET


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
    llm_budget: int | None = Query(
        None,
        ge=0,
        le=100,
        description=(
            "Override the pulse LLM call cap for THIS run only — 0 "
            "demonstrates the rule-based fallback lane live."
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

    budget_limit = llm_budget if llm_budget is not None else pulse_llm_budget()
    with llm_call_budget(budget_limit) as budget:
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

        # Unified detection: the security and fraud lanes run in the same
        # sweep and persist their own signals; neither feeds an LLM agent
        # (operator-facing lanes by locked decision).
        security_report = scan_security()
        persist_signals(conn, security_report.signals)
        fraud_flagged = [
            signal for signal in fraud.score_events() if signal.band != "clear"
        ]
        fraud.persist_flagged(conn, fraud_flagged)

        # Chronicler: one budgeted call narrates the run for the operator.
        # The facts are computed HERE, in Python — the agent only restates
        # them; a dry budget lands on its deterministic fallback narrative.
        top = max(anomalies, key=lambda a: abs(a.z_score), default=None)
        facts = {
            "cost_signals": len(links),
            "security_signals": security_report.signal_count,
            "fraud_flagged": len(fraud_flagged),
            "analyzed": analyzed_count,
            "proposals_filed": filed_count,
            "proposals_reused": reused_count,
            "llm_budget": budget_limit,
            "llm_calls_used": budget.used,
            "top_service": top.service if top else None,
        }
        briefing = PulseBriefing(**write_briefing(conn, facts))

    if budget.exhausted:
        logger.warning(
            "pulse llm budget exhausted after %d call(s); remaining agents "
            "answered with rule-based fallbacks",
            budget.used,
        )

    report = PulseReport(
        threshold=threshold,
        mission=mission_name,
        reflex_ms=reflex_ms,
        signals=len(links),
        security_signals=security_report.signal_count,
        fraud_signals=len(fraud_flagged),
        analyzed=analyzed_count,
        proposals_filed=filed_count,
        proposals_reused=reused_count,
        llm_budget=budget_limit,
        llm_calls_used=budget.used,
        budget_exhausted=budget.exhausted,
        briefing=briefing,
        chain=links,
    )
    # Persist the run so a page reload (or a colleague's later look) can
    # replay the last chain and its briefing instead of losing the story.
    with db.writing(conn):
        conn.execute(
            "INSERT INTO pulse_log (report_json) VALUES (?)",
            (report.model_dump_json(),),
        )
    return report


@router.get("/pulse/last", responses={404: {"description": "No pulse has run yet."}})
def last_pulse(conn: sqlite3.Connection = Depends(db.get_db)) -> LastPulseReport:
    """Return the most recent pulse run (report + briefing), if any."""
    row = conn.execute(
        "SELECT report_json, created_at FROM pulse_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no pulse has run yet")
    return LastPulseReport(
        ran_at=row["created_at"],
        report=PulseReport.model_validate_json(row["report_json"]),
    )
