"""Fraud mission (Sprint 3, S3-②) — deliberately minimal by plan.

A YAML mission (configs/fraud.yaml), a mock event feed and a SIMPLE
deterministic rule score — nothing more in the competition window. Every
figure is arithmetic over the published rules below; every non-clear
signal is a SUGGESTION for operator review. Nothing here (or anywhere)
blocks a payment automatically — human-in-the-loop is the product's
spine, and the fraud lane inherits it unchanged.

Scoring rules (published, reproducible by hand):

- amount vs the account's typical amount: ratio >= 10 -> +40,
  >= 3 -> +25, >= 1.5 -> +10
- velocity: >= 5 transactions in 10 minutes -> +25, >= 3 -> +15
- geography: transaction country differs from home country -> +20
- account age: younger than the configured cutoff (default 30 days) -> +15

Total clamps to 100. The band thresholds (default >= 70 hold_suggested,
>= 40 review, otherwise clear) and the new-account cutoff are mission
configuration (configs/fraud.yaml ``rules``); the point values stay code
constants so the arithmetic remains a published contract. Every hit is
returned structured (rule / points / detail), so the sum is auditable
line by line.

Flagged (non-clear) signals persist into the shared event store as their
own kind — like the security lane, the scan is the ingestion point. They
are NEVER routed into the cost agents and never enter the HITL funnel
arithmetic (both filter on kind).
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query

from app import bus, db
from app.detection import shift_iso, demo_rebase_delta
from app.missions import MissionError, get_mission
from app.models import FraudRuleHit, FraudSignal, FraudSignalReport

logger = logging.getLogger("cloudsentinel.fraud")

router = APIRouter(prefix="/fraud", tags=["fraud"])

FRAUD_DATA_FILE = Path(__file__).parent / "data" / "mock_fraud_events.json"

EVENT_KIND = "fraud_signal"

# Fallbacks only: the operative thresholds live in the fraud mission's
# ``rules`` block; these constants answer when no mission is loadable.
HOLD_BAND = 70
REVIEW_BAND = 40
NEW_ACCOUNT_DAYS = 30

NOTE = (
    "simple deterministic rule score over mock events — every non-clear "
    "signal is a suggestion for operator review; nothing is blocked "
    "automatically (human-in-the-loop)"
)

_warned_mission_fallback = False


def load_fraud_dataset() -> dict:
    with FRAUD_DATA_FILE.open() as f:
        dataset = json.load(f)
    # Same whole-week demo shift as the cost lane (see demo_rebase_delta).
    delta = demo_rebase_delta()
    if delta:
        for event in dataset["events"]:
            event["date"] = shift_iso(event["date"], delta)
    return dataset


def resolve_rules() -> tuple[int, int, int]:
    """(hold_band, review_band, new_account_days) — mission first.

    The fallback is logged once per process, mirroring the recommender's
    debate-threshold pattern: silently ignoring a configured rule set
    forever would mask a broken config.
    """
    global _warned_mission_fallback
    try:
        rules = get_mission("fraud").rules
    except MissionError:
        rules = None
        if not _warned_mission_fallback:
            logger.warning(
                "fraud mission unavailable; using built-in rule thresholds",
                exc_info=True,
            )
            _warned_mission_fallback = True
    if rules is None:
        return HOLD_BAND, REVIEW_BAND, NEW_ACCOUNT_DAYS
    return rules.hold_band, rules.review_band, rules.new_account_days


def score_breakdown(
    event: dict, new_account_days: int = NEW_ACCOUNT_DAYS
) -> tuple[int, list[FraudRuleHit]]:
    """Deterministic rule score with the structured hits that produced it."""
    hits: list[FraudRuleHit] = []

    typical = float(event.get("typical_amount", 0.0)) or 1.0
    ratio = float(event.get("amount", 0.0)) / typical
    amount_points = 40 if ratio >= 10 else 25 if ratio >= 3 else 10 if ratio >= 1.5 else 0
    if amount_points:
        hits.append(
            FraudRuleHit(
                rule="amount",
                points=amount_points,
                detail=f"amount {ratio:.1f}x the account's typical",
            )
        )

    velocity = int(event.get("tx_last_10m", 0))
    velocity_points = 25 if velocity >= 5 else 15 if velocity >= 3 else 0
    if velocity_points:
        hits.append(
            FraudRuleHit(
                rule="velocity",
                points=velocity_points,
                detail=f"{velocity} transactions in 10 minutes",
            )
        )

    if event.get("country") != event.get("home_country"):
        hits.append(
            FraudRuleHit(
                rule="geography",
                points=20,
                detail=(
                    f"transaction from {event.get('country')} against home "
                    f"{event.get('home_country')}"
                ),
            )
        )

    if int(event.get("account_age_days", 10**6)) < new_account_days:
        hits.append(
            FraudRuleHit(
                rule="account_age",
                points=15,
                detail=f"account only {event.get('account_age_days')} days old",
            )
        )

    return min(sum(hit.points for hit in hits), 100), hits


def simple_score(
    event: dict, new_account_days: int = NEW_ACCOUNT_DAYS
) -> tuple[int, list[str]]:
    """Deterministic rule score with plain-text reasons (compat shape)."""
    score, hits = score_breakdown(event, new_account_days)
    return score, [hit.detail for hit in hits]


def band_for(
    score: int, hold_band: int = HOLD_BAND, review_band: int = REVIEW_BAND
) -> str:
    if score >= hold_band:
        return "hold_suggested"
    if score >= review_band:
        return "review"
    return "clear"


def score_events() -> list[FraudSignal]:
    """Score every mock event under the mission's rules; highest first."""
    hold_band, review_band, new_account_days = resolve_rules()
    dataset = load_fraud_dataset()
    signals = []
    for event in dataset["events"]:
        score, hits = score_breakdown(event, new_account_days)
        signals.append(
            FraudSignal(
                id=str(event["id"]),
                date=str(event["date"]),
                service=str(event.get("service", "payments")),
                amount=float(event["amount"]),
                score=score,
                band=band_for(score, hold_band, review_band),
                reasons=[hit.detail for hit in hits],
                rule_hits=hits,
            )
        )
    signals.sort(key=lambda signal: signal.score, reverse=True)
    return signals


def persist_flagged(conn: sqlite3.Connection, signals: list[FraudSignal]) -> None:
    """Upsert each non-clear signal as a fraud event (stable natural key).

    The transaction id rides the events table's subject column: it is the
    signal's natural identity (several transactions can share a service
    and a day). Emits the shared [SIGNAL] tagged log line with the kind
    field carrying the lane.
    """
    if not signals:
        return
    with db.writing(conn):
        for signal in signals:
            db.upsert_event(
                conn,
                kind=EVENT_KIND,
                service=signal.id,
                occurred_on=signal.date,
                payload_json=signal.model_dump_json(),
            )
    for signal in signals:
        logger.info(
            "[SIGNAL] %s",
            json.dumps(
                {
                    "kind": EVENT_KIND,
                    "id": signal.id,
                    "date": signal.date,
                    "score": signal.score,
                    "band": signal.band,
                },
                sort_keys=True,
            ),
        )


def scan_and_persist(
    conn: sqlite3.Connection,
) -> tuple[list[FraudSignal], list[FraudSignal]]:
    """Score the feed once and persist every non-clear signal.

    Returns ``(all_signals, flagged)`` so callers that need the full band
    histogram (the /fraud/signals endpoint) and callers that only need the
    flagged set (the pulse sweep) share the same score→filter→persist step.
    """
    signals = score_events()
    flagged = [signal for signal in signals if signal.band != "clear"]
    persist_flagged(conn, flagged)
    return signals, flagged


HOLD_ACTION_NOTE = (
    "suggestion only — approving records the operator's hold decision in the "
    "audit trail; no payment is ever blocked automatically"
)


def file_hold_actions(conn: sqlite3.Connection, signals: list[FraudSignal]) -> int:
    """File one HITL inbox card per hold_suggested signal (no LLM involved).

    Three missions, one decision box: the fraud lane's strongest signals
    now meet the same operator gate the cost lane uses. The card is pure
    rule arithmetic — the deterministic score, its per-rule hits and the
    advisory note — and the reuse guard keeps one open card per signal
    across re-sweeps. Rejected cards may be re-filed on a later sweep,
    mirroring the cost lane's reuse semantics.
    """
    holds = [signal for signal in signals if signal.band == "hold_suggested"]
    if not holds:
        return 0
    filed_signals: list[FraudSignal] = []
    with db.writing(conn):
        for signal in holds:
            event = conn.execute(
                "SELECT id FROM events WHERE kind = ? AND service = ? "
                "AND occurred_on = ?",
                (EVENT_KIND, signal.id, signal.date),
            ).fetchone()
            if event is None:
                continue  # not persisted yet — the next sweep files it
            open_card = conn.execute(
                "SELECT 1 FROM actions WHERE event_id = ? AND state != 'rejected' "
                "LIMIT 1",
                (event["id"],),
            ).fetchone()
            if open_card is not None:
                continue
            detail = {
                "kind": "fraud_hold",
                "category": "FRAUD_REVIEW",
                "fraud": signal.model_dump(),
                "note": HOLD_ACTION_NOTE,
            }
            conn.execute(
                "INSERT INTO actions (event_id, title, detail_json) VALUES (?, ?, ?)",
                (
                    event["id"],
                    f"Review payment {signal.id} — hold suggested (score {signal.score})",
                    json.dumps(detail),
                ),
            )
            filed_signals.append(signal)
    for signal in filed_signals:
        logger.info(
            "[FRAUD] %s",
            json.dumps(
                {"id": signal.id, "score": signal.score, "band": signal.band},
                sort_keys=True,
            ),
        )
        bus.emit(
            conn,
            "fraud-watch",
            "hold",
            f"{signal.id}: hold card filed (rule score {signal.score}) — "
            "suggestion only, the operator decides",
        )
    return len(filed_signals)


@router.get("/signals")
def get_fraud_signals(
    band: Literal["clear", "review", "hold_suggested"] | None = Query(
        None, description="If set, only return signals in this band."
    ),
    min_score: int | None = Query(
        None, ge=0, le=100, description="If set, only return signals scoring at least this."
    ),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> FraudSignalReport:
    """Score the mock payment feed with the published deterministic rules.

    Like the other lanes, the scan is also the ingestion point: every
    non-clear signal persists with a stable event identity regardless of
    any filter. ``count`` and ``bands`` describe ALL scored events, so
    filtered views stay comparable.
    """
    try:
        mission_name = get_mission("fraud").mission
    except MissionError:
        logger.warning("fraud mission unavailable; scoring without mission tags")
        mission_name = None
    signals, flagged = scan_and_persist(conn)

    bands: dict[str, int] = {"clear": 0, "review": 0, "hold_suggested": 0}
    for signal in signals:
        bands[signal.band] += 1

    visible = [
        signal
        for signal in signals
        if (band is None or signal.band == band)
        and (min_score is None or signal.score >= min_score)
    ]
    return FraudSignalReport(
        mission=mission_name,
        note=NOTE,
        count=len(flagged),
        bands=bands,
        signals=visible,
    )
