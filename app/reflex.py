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

The learning loop stays HITL-sacred: ``suggest_reflex_rules`` and
``propose_reflex_rules`` only *propose* rules mined from decision memory;
nothing here (or anywhere) auto-approves. Adopting a suggestion is an
operator decision — the machine drafts the reflex, the human enacts it.
That asymmetry is the whole product thesis, so it is enforced by there
being no adoption code path at all, not by a flag someone could flip.
"""

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app import db, history
from app.detection import (
    DETECTOR_ENV,
    DETECTORS,
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
    if raw in DETECTORS:
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
    leave_one_out: bool | None = None,
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
        leave_one_out=leave_one_out,
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


def _signature(context: dict) -> tuple[str, str, str, str] | None:
    """The anomaly's fingerprint, or None when the row carries no anomaly.

    Four coordinates, all deterministic and all already persisted with the
    proposal: WHICH service, HOW severe the detector called it, WHICH WAY
    the spend moved, and WHAT KIND of remedy was recommended. A rule mined
    across a coarser key would fire on situations the operators never
    actually saw.
    """
    anomaly = context.get("anomaly")
    if not isinstance(anomaly, dict):
        return None
    service = anomaly.get("service")
    severity = anomaly.get("severity")
    if not isinstance(service, str) or not isinstance(severity, str):
        return None
    try:
        z_score = float(anomaly["z_score"])
    except (KeyError, TypeError, ValueError):
        return None
    direction = "spike" if z_score >= 0 else "drop"
    category = context.get("category")
    return (
        service,
        severity,
        direction,
        category if isinstance(category, str) else "uncategorised",
    )


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 2)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 2)


def propose_reflex_rules(
    conn: sqlite3.Connection,
    *,
    min_approvals: int = SUGGESTION_MIN_APPROVALS,
    window_days: int = SUGGESTION_WINDOW_DAYS,
) -> tuple[list[dict], int]:
    """Mine settled anomaly signatures into candidate reflex rules.

    The neuroplasticity loop: when the same service, severity, direction
    and remedy category have been approved ``min_approvals`` times with
    the same preferred stance and never rejected, the system drafts the
    rule it *would* have followed — condition, threshold, rationale, and
    the decision ids the draft rests on.

    A signature carrying any rejection, or a mix of stances, is CONTESTED
    and yields nothing: the operators have not settled it, so neither has
    the machine. Returns ``(rules, contested_count)`` — the contested
    tally is published rather than dropped, so a quiet "no rules today"
    can be told apart from "we hid the disagreement".

    Nothing here activates anything. The return value is a draft for a
    human to adopt, amend, or throw away.
    """
    rows = conn.execute(
        "SELECT id, action_id, service, verdict, rationale, input_context_json "
        "FROM decisions WHERE created_at >= datetime('now', ?) ORDER BY id",
        (f"-{window_days} days",),
    ).fetchall()

    buckets: dict[tuple[str, str, str, str], dict] = {}
    for row in rows:
        try:
            context = json.loads(row["input_context_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(context, dict):
            continue
        signature = _signature(context)
        if signature is None:
            continue  # seeded verdicts and non-cost lanes carry no anomaly
        bucket = buckets.setdefault(
            signature,
            {
                "approvals": [],
                "rejections": 0,
                "stances": set(),
                "z_scores": [],
                "action_ids": [],
                "rationales": [],
                "titles": set(),
            },
        )
        if row["verdict"] != "approved":
            bucket["rejections"] += 1
            continue
        bucket["approvals"].append(row["id"])
        stance = context.get("preferred")
        bucket["stances"].add(stance if isinstance(stance, str) else "UNSTATED")
        try:
            bucket["z_scores"].append(abs(float(context["anomaly"]["z_score"])))
        except (KeyError, TypeError, ValueError):
            pass
        if row["action_id"] is not None:
            bucket["action_ids"].append(row["action_id"])
        if row["rationale"]:
            bucket["rationales"].append(row["rationale"])
        for option in context.get("options") or []:
            if isinstance(option, dict) and option.get("stance") == stance:
                title = option.get("title")
                if isinstance(title, str):
                    bucket["titles"].add(title)

    rules: list[dict] = []
    contested = 0
    for signature, bucket in sorted(buckets.items()):
        service, severity, direction, category = signature
        if len(bucket["approvals"]) < min_approvals:
            continue
        if bucket["rejections"] or len(bucket["stances"]) > 1:
            contested += 1
            continue
        stance = next(iter(bucket["stances"]))
        # The most conservative bar that still covers every approval: a
        # threshold at the mean would propose a rule for cases the humans
        # have not actually seen.
        threshold = round(min(bucket["z_scores"]), 2) if bucket["z_scores"] else None
        latencies = history.deliberation_hours(conn, bucket["action_ids"])
        rules.append(
            {
                "signature": {
                    "service": service,
                    "severity": severity,
                    "direction": direction,
                    "category": category,
                },
                "condition": (
                    f"{severity} {direction} on {service} recommending "
                    f"'{category}'"
                    + (f" with |z| ≥ {threshold}" if threshold is not None else "")
                ),
                "threshold": threshold,
                "proposed_action": (
                    f"pre-approve the {stance} option"
                    + (
                        f" — “{sorted(bucket['titles'])[0]}”"
                        if bucket["titles"]
                        else ""
                    )
                ),
                "stance": stance,
                "approvals": len(bucket["approvals"]),
                "rejections": bucket["rejections"],
                "decision_ids": bucket["approvals"],
                "action_ids": sorted(bucket["action_ids"]),
                "median_deliberation_hours": _median(list(latencies.values())),
                "window_days": window_days,
                "rationale": (
                    f"operators approved this signature {len(bucket['approvals'])} "
                    f"times in {window_days} days and rejected it never, always "
                    f"choosing {stance}"
                    + (
                        f"; most recently “{bucket['rationales'][-1]}”"
                        if bucket["rationales"]
                        else ""
                    )
                ),
                "status": "proposed",
                "activation": (
                    "requires an operator to adopt it — the machine drafts "
                    "reflex rules, it never enacts them"
                ),
            }
        )
    return rules, contested


class ReflexSuggestion(BaseModel):
    service: str
    approvals: int
    rejections: int
    window_days: int
    suggestion: str


class RuleSignature(BaseModel):
    service: str
    severity: str
    direction: str
    category: str


class ProposedRule(BaseModel):
    signature: RuleSignature
    condition: str
    threshold: float | None
    proposed_action: str
    stance: str
    approvals: int
    rejections: int
    decision_ids: list[int]
    action_ids: list[int]
    median_deliberation_hours: float | None
    window_days: int
    rationale: str
    status: str
    activation: str


class ReflexSuggestionsReport(BaseModel):
    count: int
    window_days: int
    min_approvals: int
    note: str
    suggestions: list[ReflexSuggestion]
    proposed_rules: list[ProposedRule]
    contested_signatures: int


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
    rules, contested = propose_reflex_rules(
        conn, min_approvals=min_approvals, window_days=window_days
    )
    return ReflexSuggestionsReport(
        count=len(mined),
        window_days=window_days,
        min_approvals=min_approvals,
        note=(
            "suggestions only — adopting a reflex rule is an operator "
            "decision (human-in-the-loop); proposed_rules are drafts keyed "
            "to the anomaly signature, suggestions are the coarser "
            "per-service read of the same memory"
        ),
        suggestions=[ReflexSuggestion(**suggestion) for suggestion in mined],
        proposed_rules=[ProposedRule(**rule) for rule in rules],
        contested_signatures=contested,
    )
