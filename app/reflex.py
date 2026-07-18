"""Reflex engine — the deterministic fast lane (Sprint 3, S3-①).

The dual-loop design splits every scan into two speeds: the REFLEX pass
(pure statistics, sub-millisecond, no model anywhere near it) and the
conscious loop (Analyst → debate-lite → Recommender → human). This
module is the reflex: it resolves the mission's detection settings,
runs the detector, and reports how long the reflex actually took so the
dashboard can wear the measured "REFLEX X ms" badge instead of a claim.

Precedence for detection settings: explicit caller argument (the API's
``threshold`` query) > environment override (``SENTINEL_DETECTOR`` /
``SENTINEL_BASELINE_WINDOW_DAYS`` / ``SENTINEL_SEASONAL`` — the ops
escape hatch) > mission YAML > code default. The environment wins over
the mission file so an operator can flip a detector live without
editing configs.

The learning loop stays HITL-sacred: ``suggest_reflex_rules`` only
*proposes* rules mined from decision memory; nothing here (or anywhere)
auto-approves. Adopting a suggestion is an operator decision.
"""

import logging
import os
import sqlite3
import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app import db
from app.detection import (
    DETECTOR_ENV,
    MIN_HISTORY,
    SEASONAL_ENV,
    WINDOW_ENV,
    DetectionRun,
    run_detection,
)
from app.missions import MissionConfig

logger = logging.getLogger("cloudsentinel.reflex")

router = APIRouter(prefix="/reflex", tags=["reflex"])

SUGGESTION_MIN_APPROVALS = 3
SUGGESTION_WINDOW_DAYS = 30


@dataclass
class ReflexResult:
    mission: str
    run: DetectionRun
    latency_ms: float
    threshold: float  # the resolved threshold the pass actually used


def _env_detector() -> str | None:
    """A VALID env override, or None to fall through to the mission.

    An invalid value must not silently veto both the operator's intent and
    the mission file down to code defaults — it is logged and ignored.
    """
    raw = os.environ.get(DETECTOR_ENV, "").strip().lower()
    if not raw:
        return None
    if raw in ("zscore", "mad"):
        return raw
    logger.warning("ignoring invalid %s=%r; using the mission value", DETECTOR_ENV, raw)
    return None


def _env_window() -> int | None:
    raw = os.environ.get(WINDOW_ENV, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value >= MIN_HISTORY:
        return value
    logger.warning("ignoring invalid %s=%r; using the mission value", WINDOW_ENV, raw)
    return None


def _env_seasonal() -> bool | None:
    raw = os.environ.get(SEASONAL_ENV, "").strip().lower()
    if not raw:
        return None
    if raw in ("1", "true"):
        return True
    if raw in ("0", "false"):
        return False
    logger.warning("ignoring invalid %s=%r; using the mission value", SEASONAL_ENV, raw)
    return None


def reflex_scan(
    records: list,
    mission: MissionConfig,
    threshold: float | None = None,
) -> ReflexResult:
    """Run the mission's detection pass and measure its wall-clock cost."""
    detection = mission.detection
    resolved_threshold = threshold if threshold is not None else detection.threshold
    env_detector = _env_detector()
    env_window = _env_window()
    env_seasonal = _env_seasonal()
    started = time.perf_counter()
    run = run_detection(
        records,
        resolved_threshold,
        detector=env_detector if env_detector is not None else detection.detector,
        window=env_window if env_window is not None else detection.baseline_window_days,
        seasonal=env_seasonal if env_seasonal is not None else detection.seasonal,
        critical_z=detection.critical_z,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    return ReflexResult(
        mission=mission.mission,
        run=run,
        latency_ms=round(latency_ms, 2),
        threshold=resolved_threshold,
    )


def suggest_reflex_rules(
    conn: sqlite3.Connection,
    *,
    min_approvals: int = SUGGESTION_MIN_APPROVALS,
    window_days: int = SUGGESTION_WINDOW_DAYS,
) -> list[dict]:
    """Mine decision memory for services the operators always approve.

    A service with ``min_approvals``+ approvals and zero rejections in
    the window is a candidate for a future Reflex rule ("this pattern
    has never needed deliberation"). Returned as SUGGESTIONS with the
    evidence counts — never applied automatically.
    """
    rows = conn.execute(
        "SELECT service, "
        "SUM(CASE WHEN verdict = 'approved' THEN 1 ELSE 0 END) AS approvals, "
        "SUM(CASE WHEN verdict = 'rejected' THEN 1 ELSE 0 END) AS rejections "
        "FROM decisions WHERE created_at >= datetime('now', ?) "
        "GROUP BY service ORDER BY service",
        (f"-{window_days} days",),
    ).fetchall()
    suggestions = []
    for row in rows:
        if row["approvals"] >= min_approvals and row["rejections"] == 0:
            suggestions.append(
                {
                    "service": row["service"],
                    "approvals": row["approvals"],
                    "rejections": row["rejections"],
                    "window_days": window_days,
                    "suggestion": (
                        f"candidate reflex rule: operators approved every "
                        f"{row['approvals']} proposals for {row['service']} in "
                        f"{window_days} days — consider a pre-approved playbook "
                        "for this pattern"
                    ),
                }
            )
    return suggestions


class ReflexSuggestion(BaseModel):
    service: str
    approvals: int
    rejections: int
    window_days: int
    suggestion: str


class ReflexSuggestionsReport(BaseModel):
    count: int
    window_days: int
    min_approvals: int
    note: str
    suggestions: list[ReflexSuggestion]


@router.get("/suggestions")
def reflex_suggestions(
    window_days: int = Query(
        30, ge=1, le=365, description="How far back to mine decision memory."
    ),
    min_approvals: int = Query(
        3, ge=2, le=50, description="Unanimous approvals required to suggest a rule."
    ),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> ReflexSuggestionsReport:
    """Rules the learning loop would propose — never applied automatically.

    The Arena→Reflex learning loop, kept HITL-sacred: decision memory is
    mined for patterns the operators have always approved, and the result
    is a SUGGESTION list for a human to review. Nothing here changes any
    behavior by itself.
    """
    mined = suggest_reflex_rules(
        conn, min_approvals=min_approvals, window_days=window_days
    )
    return ReflexSuggestionsReport(
        count=len(mined),
        window_days=window_days,
        min_approvals=min_approvals,
        note=(
            "suggestions only — adopting a reflex rule is an operator "
            "decision (human-in-the-loop)"
        ),
        suggestions=[ReflexSuggestion(**suggestion) for suggestion in mined],
    )
