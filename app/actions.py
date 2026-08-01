"""Human-in-the-loop action lifecycle (Sprint 2, WP-5a).

State machine (docs/architecture.md, binding):

    proposed -> approved | rejected -> executed (simulated, WP-5b)
    rejected -> proposed (reopen — the hand reconsiders)

Approve/reject are the operator decisions; every transition is persisted
with timestamp and actor, and lands on the append-only ``action_events``
trail the decision desk renders as a per-card timeline. The decision POST
endpoints honor an optional
``Idempotency-Key`` header: the key is claimed inside the same write
transaction as the state change, so a retried or double-clicked decision
replays the first response instead of failing or double-executing. Keys
are scoped to action id + verb, so the same client key on a different
action (or on approve vs reject) can never replay a foreign response.

Only successful decisions are stored for replay: a 404/409 rolls the
whole transaction back (claim included), which keeps error outcomes
deterministic without persisting them.
"""

import html
import json
import logging
import math
import os
import sqlite3
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response

from app import bus, db, dispatch, history
from app.auth import UserOut, enforce_decision_auth, optional_user
from app.enrichment import (
    attack_technique,
    blast_radius_tier,
    finops_capability,
    verification_plan,
)
from app.logstream import log_tag
from app.runbooks import match_runbooks
from app.models import (
    ActionDecisionRequest,
    ActionHistoryEntry,
    ActionListReport,
    ActionRecord,
    ActionState,
    FrameworkTag,
)

logger = logging.getLogger("cloudsentinel.actions")

router = APIRouter(prefix="/actions", tags=["actions"])

DECIDABLE_STATE = "proposed"

# SQLite INTEGER is a signed 64-bit; larger Python ints would raise
# OverflowError at parameter binding, so bound the path parameter instead.
ACTION_ID_PATH = Path(ge=1, le=2**63 - 1, description="Action id from GET /actions.")

DECISION_RESPONSES = {
    404: {"description": "No action with this id exists."},
    409: {
        "description": (
            "The action has already left the 'proposed' state; "
            "decisions are single-shot."
        )
    },
}

EXECUTE_RESPONSES = {
    404: {"description": "No action with this id exists."},
    409: {"description": "Only approved actions can be executed."},
}

REOPEN_RESPONSES = {
    404: {"description": "No action with this id exists."},
    409: {"description": "Only rejected (or expired) actions can be reopened."},
}

# Request-triggered timeout: the deploy target sleeps between requests, so
# no scheduler can run — stale proposals expire whenever the inbox is read.
ACTION_TTL_HOURS_ENV = "SENTINEL_ACTION_TTL_HOURS"
DEFAULT_ACTION_TTL_HOURS = 72.0
TIMEOUT_ACTOR = "system:timeout"


def action_ttl_hours() -> float:
    raw = os.environ.get(ACTION_TTL_HOURS_ENV, "").strip()
    if not raw:
        return DEFAULT_ACTION_TTL_HOURS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_ACTION_TTL_HOURS
    # nan/inf parse as floats but would silently break the SQLite datetime
    # modifier (NULL cutoff -> nothing ever expires); treat them as garbage.
    if not math.isfinite(value):
        return DEFAULT_ACTION_TTL_HOURS
    return value


def expire_stale_proposals(conn: sqlite3.Connection) -> int:
    """Reject proposals older than the TTL, attributed to the system actor.

    A TTL of 0 (or negative) disables expiry. The pre-check keeps the
    common no-op path free of write locks.
    """
    hours = action_ttl_hours()
    if hours <= 0:
        return 0
    cutoff_modifier = f"-{hours} hours"
    stale = conn.execute(
        "SELECT 1 FROM actions WHERE state = 'proposed' "
        "AND proposed_at < datetime('now', ?) LIMIT 1",
        (cutoff_modifier,),
    ).fetchone()
    if stale is None:
        return 0
    with db.writing(conn):
        rows = conn.execute(
            "SELECT id FROM actions WHERE state = 'proposed' "
            "AND proposed_at < datetime('now', ?)",
            (cutoff_modifier,),
        ).fetchall()
        if not rows:
            return 0
        ids = [row["id"] for row in rows]
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            "UPDATE actions SET state = 'rejected', "
            "decided_at = datetime('now'), decided_by = ? "
            f"WHERE id IN ({placeholders})",
            [TIMEOUT_ACTOR, *ids],
        )
        for action_id in ids:
            history.record(
                conn,
                action_id,
                "expired",
                TIMEOUT_ACTOR,
                "proposal timed out unanswered",
            )
        return len(ids)


# --- alert suppression: one open card speaks for a service on a lane ------
#
# Per-event dedupe (every filing site already has it) stops the SAME signal
# minting a second card. It does nothing about the operator's real burden:
# a service that deviates again tomorrow, and again the day after, opens a
# fresh card each time while the first one is still unanswered. Suppression
# closes that gap — while an open card speaks for a service on a lane, later
# signals on that lane fold INTO it as a counted repeat instead of becoming
# inbox noise. Nothing is discarded: the count and the folded dates ride on
# the card, so the operator sees "this is the third day" at a glance.
SUPPRESSION_WINDOW_ENV = "SENTINEL_SUPPRESSION_WINDOW_HOURS"
DEFAULT_SUPPRESSION_WINDOW_HOURS = 24.0
SUPPRESSION_ACTOR = "system:suppression"

# Only an UNDECIDED card suppresses. A repeat folds into a question the
# human has not answered yet; the moment they approve, reject or execute,
# that conversation is closed and the next signal has earned its own card.
# Folding into a decided card would quietly apply an old verdict to a new
# fact — the one thing a human-in-the-loop system must never do.
OPEN_STATES = ("proposed",)

# The folded repeats ride inside detail_json, so cap the sample list: a
# long-running deployment must not grow one row without bound.
SUPPRESSION_SAMPLE_CAP = 20


def suppression_window_hours() -> float:
    """How long an open card keeps speaking for its service; <= 0 disables.

    Same defensive parse as the TTL knob: garbage and non-finite values
    fall back to the default rather than silently disabling the feature.
    """
    raw = os.environ.get(SUPPRESSION_WINDOW_ENV, "").strip()
    if not raw:
        return DEFAULT_SUPPRESSION_WINDOW_HOURS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_SUPPRESSION_WINDOW_HOURS
    if not math.isfinite(value):
        return DEFAULT_SUPPRESSION_WINDOW_HOURS
    return value


def find_suppressor(
    conn: sqlite3.Connection,
    *,
    kind: str,
    service: str,
    exclude_event_id: int | None = None,
) -> sqlite3.Row | None:
    """The open card already speaking for this service on this lane, if any.

    Scoped by event *kind* on purpose: a cost card must never silence a
    fraud hold for the same service on the same day — those are different
    conversations, and the cross-lane correlation is a finding of its own.
    """
    hours = suppression_window_hours()
    if hours <= 0 or not kind or not service:
        return None
    placeholders = ",".join("?" for _ in OPEN_STATES)
    sql = (
        "SELECT a.* FROM actions a JOIN events e ON e.id = a.event_id "
        "WHERE e.kind = ? AND e.service = ? COLLATE NOCASE "
        f"AND a.state IN ({placeholders}) "
        "AND a.proposed_at >= datetime('now', ?) "
    )
    params: list = [kind, service, *OPEN_STATES, f"-{hours} hours"]
    if exclude_event_id is not None:
        sql += "AND a.event_id != ? "
        params.append(exclude_event_id)
    sql += "ORDER BY a.id DESC LIMIT 1"
    return conn.execute(sql, params).fetchone()


def _suppression_block(detail: dict) -> dict:
    block = detail.get("suppression")
    return block if isinstance(block, dict) else {}


def suppressed_count(detail: dict) -> int:
    """How many repeats this card has absorbed; 0 for cards that predate it."""
    try:
        return int(_suppression_block(detail).get("suppressed_count") or 0)
    except (TypeError, ValueError):
        return 0


def record_suppression(
    conn: sqlite3.Connection,
    action_row: sqlite3.Row,
    *,
    occurred_on: str | None = None,
    z_score: float | None = None,
) -> int:
    """Fold a repeat signal into an open card; returns the new repeat count.

    Joins the caller's open write transaction (``db.writing`` is not
    reentrant, and the filing sites already hold one) — deciding not to
    file and recording why belong to the same commit.
    """
    try:
        detail = json.loads(action_row["detail_json"])
    except (TypeError, ValueError):
        detail = {}
    if not isinstance(detail, dict):
        detail = {}
    block = _suppression_block(detail)
    count = suppressed_count(detail) + 1
    repeats = block.get("repeats")
    repeats = list(repeats) if isinstance(repeats, list) else []
    repeats.append({"date": occurred_on, "z_score": z_score})
    detail["suppression"] = {
        "suppressed_count": count,
        "window_hours": suppression_window_hours(),
        "repeats": repeats[-SUPPRESSION_SAMPLE_CAP:],
    }
    action_id = action_row["id"]
    conn.execute(
        "UPDATE actions SET detail_json = ? WHERE id = ?",
        (json.dumps(detail), action_id),
    )
    note = f"repeat signal{f' on {occurred_on}' if occurred_on else ''} folded in"
    if z_score is not None:
        note += f" (z {z_score})"
    history.record(conn, action_id, "suppressed", SUPPRESSION_ACTOR, note)
    bus.emit(
        conn,
        "operator",
        "suppressed",
        f"repeat signal folded into action #{action_id} — "
        f"{count} suppressed, the inbox stays one card",
    )
    log_tag(
        logger,
        "[SUPPRESSED]",
        action_id=action_id,
        suppressed_count=count,
        occurred_on=occurred_on,
    )
    return count


def _expires_in_hours(row: sqlite3.Row) -> float | None:
    """Hours until the request-triggered TTL expires this proposal.

    None for decided actions, a disabled TTL, or an unparseable timestamp.
    The figure can dip slightly negative between the moment a proposal
    crosses the cutoff and the read that sweeps it — honest, not clamped.
    """
    if row["state"] != DECIDABLE_STATE:
        return None
    ttl = action_ttl_hours()
    if ttl <= 0:
        return None
    try:
        proposed = datetime.strptime(row["proposed_at"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None
    age_hours = (datetime.now(timezone.utc) - proposed).total_seconds() / 3600
    return round(ttl - age_hours, 1)


def _framework_tag(detail: dict) -> FrameworkTag | None:
    """The industry reference for a card, read off the category it carries.

    Every filing site stamps a category — the cost lane's four, the fraud
    lane's FRAUD_REVIEW, the budget guard's BUDGET_GUARD — so one lookup
    covers all of them. A card from before categories existed gets None
    rather than a guessed reference.
    """
    category = detail.get("category")
    if not isinstance(category, str) or not category:
        return None
    if category.strip().upper() == "FRAUD_REVIEW":
        return attack_technique(fraud=True)
    return finops_capability(category)


def _to_record(
    row: sqlite3.Row, trail: list[ActionHistoryEntry] | None = None
) -> ActionRecord:
    detail = json.loads(row["detail_json"])
    return ActionRecord(
        id=row["id"],
        event_id=row["event_id"],
        title=row["title"],
        detail=detail,
        state=row["state"],
        proposed_at=row["proposed_at"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
        executed_at=row["executed_at"],
        expires_in_hours=_expires_in_hours(row),
        # Lifted out of detail so the inbox can badge "3 repeats folded in"
        # without every reader having to know the detail schema.
        suppressed_count=suppressed_count(detail if isinstance(detail, dict) else {}),
        framework=_framework_tag(detail if isinstance(detail, dict) else {}),
        history=trail or [],
    )


@router.get("")
def list_actions(
    state: ActionState | None = Query(
        None, description="If set, only return actions in this lifecycle state."
    ),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> ActionListReport:
    """Return proposed/decided actions for the operator inbox and ledger."""
    expire_stale_proposals(conn)
    if state is not None:
        rows = conn.execute(
            "SELECT * FROM actions WHERE state = ? ORDER BY id", (state,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM actions ORDER BY id").fetchall()
    trails = history.for_actions(conn, [row["id"] for row in rows])
    records = [_to_record(row, trails.get(row["id"])) for row in rows]
    return ActionListReport(count=len(records), actions=records)


def _record_decision(
    conn: sqlite3.Connection, row: sqlite3.Row, verdict: str, rationale: str | None
) -> None:
    """Append the operator verdict to decision memory (same transaction).

    Timeout expiries deliberately bypass this: memory holds human intent,
    and a proposal nobody answered carries none.
    """
    service = None
    if row["event_id"] is not None:
        event = conn.execute(
            "SELECT service FROM events WHERE id = ?", (row["event_id"],)
        ).fetchone()
        service = event["service"] if event else None
    if service is None:
        try:
            service = json.loads(row["detail_json"]).get("anomaly", {}).get("service")
        except (json.JSONDecodeError, AttributeError):
            service = None
    # Corrupt detail can put ANY JSON shape here; a non-string would fail
    # sqlite parameter binding and brick the decide endpoint with 500s.
    if not isinstance(service, str) or not service:
        service = "unknown"
    conn.execute(
        "INSERT INTO decisions (action_id, service, verdict, rationale, input_context_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (row["id"], service, verdict, rationale, row["detail_json"]),
    )


def _decide(
    conn: sqlite3.Connection,
    action_id: int,
    verdict: str,
    actor: str,
    idempotency_key: str | None,
    rationale: str | None = None,
) -> ActionRecord:
    scoped_key = (
        f"actions:{action_id}:{verdict}:{idempotency_key}"
        if idempotency_key is not None
        else None
    )
    with db.writing(conn):
        if scoped_key is not None:
            claimed, stored = db.claim_idempotency(conn, scoped_key)
            if not claimed and stored is not None:
                return ActionRecord.model_validate_json(stored)
            # A claimed key with no stored response cannot be produced by
            # this module (claim and store share one transaction); if it
            # ever appears, deciding normally keeps the outcome correct.
        row = conn.execute(
            "SELECT * FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"action {action_id} does not exist"
            )
        if row["state"] != DECIDABLE_STATE:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"action {action_id} is already '{row['state']}'; "
                    f"only '{DECIDABLE_STATE}' actions can be decided"
                ),
            )
        conn.execute(
            "UPDATE actions SET state = ?, decided_at = datetime('now'), "
            "decided_by = ? WHERE id = ?",
            (verdict, actor, action_id),
        )
        _record_decision(conn, row, verdict, rationale)
        history.record(conn, row["id"], verdict, actor, rationale)
        bus.emit(
            conn,
            "operator",
            "decision",
            f"action #{action_id} {verdict.upper()} by {actor}"
            + (f" — “{rationale}”" if rationale else "")
            + " · fed to decision memory",
        )
        record = _to_record(
            conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone(),
            history.for_actions(conn, [action_id]).get(action_id),
        )
        if scoped_key is not None:
            db.store_idempotency_response(conn, scoped_key, record.model_dump_json())
    # ids/enums only: the operator identity is PII on a log stream and is
    # already durably persisted in actions.decided_by for the audit trail.
    log_tag(logger, "[HITL]", action_id=action_id, transition=verdict)
    return record


def _actor(user: UserOut | None, decision: ActionDecisionRequest | None) -> str:
    """Server-derived identity wins over the request body (authoritative)."""
    if user is not None:
        return user.username
    return decision.actor if decision is not None else "operator"


@router.post("/{action_id}/approve", responses=DECISION_RESPONSES)
def approve_action(
    action_id: int = ACTION_ID_PATH,
    decision: ActionDecisionRequest | None = None,
    idempotency_key: str | None = Header(
        None, alias="Idempotency-Key", min_length=1, max_length=200
    ),
    user: UserOut | None = Depends(optional_user),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> ActionRecord:
    """Approve a proposed action; safe to retry with an Idempotency-Key.

    A valid session token makes the operator identity server-derived
    (authoritative) rather than trusting the request body. In live-ops mode
    (``SENTINEL_REQUIRE_APPROVER=1``) that session is mandatory and must
    hold the approver or admin role.
    """
    enforce_decision_auth(user)
    rationale = decision.rationale if decision is not None else None
    return _decide(
        conn, action_id, "approved", _actor(user, decision), idempotency_key, rationale
    )


@router.post("/{action_id}/reject", responses=DECISION_RESPONSES)
def reject_action(
    action_id: int = ACTION_ID_PATH,
    decision: ActionDecisionRequest | None = None,
    idempotency_key: str | None = Header(
        None, alias="Idempotency-Key", min_length=1, max_length=200
    ),
    user: UserOut | None = Depends(optional_user),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> ActionRecord:
    """Reject a proposed action; a non-empty rationale is mandatory (422).

    Safe to retry with an Idempotency-Key. A valid session token makes the
    operator identity server-derived. In live-ops mode
    (``SENTINEL_REQUIRE_APPROVER=1``) that session is mandatory and must
    hold the approver or admin role.
    """
    enforce_decision_auth(user)
    rationale = decision.rationale if decision is not None else None
    # a terminal "no" must explain itself: the rationale feeds decision
    # memory and the card's timeline, so future proposals learn from it
    if rationale is None or not rationale.strip():
        raise HTTPException(
            status_code=422,
            detail="a rejection must carry a rationale — say why the hand said no",
        )
    return _decide(
        conn,
        action_id,
        "rejected",
        _actor(user, decision),
        idempotency_key,
        rationale.strip(),
    )


@router.post("/{action_id}/reopen", responses=REOPEN_RESPONSES)
def reopen_action(
    action_id: int = ACTION_ID_PATH,
    decision: ActionDecisionRequest | None = None,
    idempotency_key: str | None = Header(
        None, alias="Idempotency-Key", min_length=1, max_length=200
    ),
    user: UserOut | None = Depends(optional_user),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> ActionRecord:
    """Reopen a rejected (or expired) action: back to the inbox, fresh TTL.

    The terminal "no" stays on the record — decision memory and the trail
    are append-only — but the hand may reconsider: state returns to
    'proposed', the decided fields clear, and the TTL clock restarts.
    Safe to retry with an Idempotency-Key. In live-ops mode
    (``SENTINEL_REQUIRE_APPROVER=1``) an approver-or-admin session is
    mandatory, like the other decision verbs.
    """
    enforce_decision_auth(user)
    actor = _actor(user, decision)
    note = decision.rationale if decision is not None else None
    scoped_key = (
        f"actions:{action_id}:reopen:{idempotency_key}"
        if idempotency_key is not None
        else None
    )
    with db.writing(conn):
        if scoped_key is not None:
            claimed, stored = db.claim_idempotency(conn, scoped_key)
            if not claimed and stored is not None:
                return ActionRecord.model_validate_json(stored)
        row = conn.execute(
            "SELECT * FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"action {action_id} does not exist"
            )
        if row["state"] != "rejected":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"action {action_id} is '{row['state']}'; "
                    "only 'rejected' actions can be reopened"
                ),
            )
        conn.execute(
            "UPDATE actions SET state = 'proposed', "
            "proposed_at = datetime('now'), decided_at = NULL, "
            "decided_by = NULL WHERE id = ?",
            (action_id,),
        )
        history.record(conn, action_id, "reopened", actor, note)
        bus.emit(
            conn,
            "operator",
            "decision",
            f"action #{action_id} REOPENED by {actor} — back to the inbox"
            + (f" — “{note}”" if note else ""),
        )
        record = _to_record(
            conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone(),
            history.for_actions(conn, [action_id]).get(action_id),
        )
        if scoped_key is not None:
            db.store_idempotency_response(conn, scoped_key, record.model_dump_json())
    log_tag(logger, "[HITL]", action_id=action_id, transition="reopened")
    return record


@router.post("/{action_id}/execute", responses=EXECUTE_RESPONSES)
def execute_action(
    action_id: int = ACTION_ID_PATH,
    idempotency_key: str | None = Header(
        None, alias="Idempotency-Key", min_length=1, max_length=200
    ),
    user: UserOut | None = Depends(optional_user),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> ActionRecord:
    """Simulated execution of an approved action.

    No real infrastructure is ever touched: the transition is recorded
    with a SIMULATION marker in the action detail, which the dashboard
    surfaces as a badge. Safe to retry with an Idempotency-Key. In
    live-ops mode (``SENTINEL_REQUIRE_APPROVER=1``) an approver-or-admin
    session is mandatory, like the other two decision verbs.
    """
    enforce_decision_auth(user)
    scoped_key = (
        f"actions:{action_id}:execute:{idempotency_key}"
        if idempotency_key is not None
        else None
    )
    with db.writing(conn):
        if scoped_key is not None:
            claimed, stored = db.claim_idempotency(conn, scoped_key)
            if not claimed and stored is not None:
                return ActionRecord.model_validate_json(stored)
        row = conn.execute(
            "SELECT * FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"action {action_id} does not exist"
            )
        if row["state"] != "approved":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"action {action_id} is '{row['state']}'; "
                    "only 'approved' actions can be executed"
                ),
            )
        detail = json.loads(row["detail_json"])
        detail["execution"] = {
            "mode": "SIMULATION",
            "note": "no real infrastructure was touched",
        }
        conn.execute(
            "UPDATE actions SET state = 'executed', "
            "executed_at = datetime('now'), detail_json = ? WHERE id = ?",
            (json.dumps(detail), action_id),
        )
        bus.emit(
            conn,
            "operator",
            "execute",
            f"action #{action_id} executed — SIMULATION, no real infrastructure touched",
        )
        history.record(
            conn,
            action_id,
            "executed",
            _actor(user, None),
            "SIMULATION — no real infrastructure touched",
        )
        record = _to_record(
            conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone(),
            history.for_actions(conn, [action_id]).get(action_id),
        )
        if scoped_key is not None:
            db.store_idempotency_response(conn, scoped_key, record.model_dump_json())
    log_tag(
        logger, "[HITL]", action_id=action_id, transition="executed", mode="SIMULATION"
    )
    # Opt-in real-world side effect, strictly AFTER the commit (db.py's
    # locked rule: no network inside an open transaction). Replayed
    # executions return above and never re-fire the webhook; a failed
    # delivery never fails the execute — see app/dispatch.py.
    dispatch.dispatch_execution(
        conn, record, lambda: _compose_report(conn, action_id) or ""
    )
    return record


def _rows(*pairs: tuple[str, object]) -> list[tuple[str, str]]:
    """Label/value pairs with empty values dropped, everything stringified."""
    return [(label, str(value)) for label, value in pairs if value not in (None, "")]


def _stance_saving(savings: dict, stance: object) -> str:
    """The computed monthly projection for one stance, or an em dash."""
    key = {"CAUTIOUS": "cautious_monthly", "BOLD": "bold_monthly"}.get(str(stance))
    value = savings.get(key) if key else None
    return "—" if value is None else str(value)


def _build_report_document(
    record: ActionRecord,
    decision: sqlite3.Row | None,
    event_kind: str | None = None,
) -> dict:
    """The incident report as structured data, before any markup.

    One document, two renderings: Markdown for a repo or a webhook payload,
    HTML for a browser or a mail thread. Building the content once is what
    keeps the two from drifting into different stories about one decision.

    Read-only and defensive throughout: every field is optional, so a
    partially populated or legacy action still renders a coherent document.
    """
    detail = record.detail if isinstance(record.detail, dict) else {}
    anomaly = detail.get("anomaly") or {}
    savings = detail.get("savings") or {}
    options = detail.get("options") or []
    execution = detail.get("execution") or {}
    transcript = detail.get("transcript") or {}
    suppression = detail.get("suppression") or {}

    sections: list[dict] = []

    if anomaly:
        framework = _framework_tag(detail) or (
            attack_technique(anomaly.get("service"))
            if event_kind and ("security" in event_kind or "fraud" in event_kind)
            else finops_capability(None)
        )
        sections.append(
            {
                "heading": "Signal",
                "rows": _rows(
                    ("Service", anomaly.get("service", "—")),
                    ("Date", anomaly.get("date", "—")),
                    (
                        "Cost",
                        f"{anomaly.get('cost', '—')} "
                        f"(baseline {anomaly.get('service_mean', '—')})",
                    ),
                    ("z-score", anomaly.get("z_score", "—")),
                    ("Severity", anomaly.get("severity", "—")),
                    ("Detector", anomaly.get("detector", "—")),
                ),
            }
        )
        sections.append(
            {
                "heading": "Triage",
                "rows": _rows(
                    ("Blast radius", blast_radius_tier(anomaly.get("z_score", 0.0))),
                    ("Framework", framework.framework),
                    (
                        "Technique" if framework.id else "Capability",
                        framework.reference,
                    ),
                    ("Reference", framework.url),
                ),
            }
        )
        matches = match_runbooks(
            f"{anomaly.get('service', '')} {event_kind or 'cost'}", limit=1
        )
        if matches:
            runbook = matches[0][0]
            sections.append(
                {
                    "heading": "Suggested runbook",
                    "lead": runbook.title,
                    "bullets": list(runbook.steps),
                }
            )

    if suppression:
        repeats = suppression.get("repeats")
        repeats = repeats if isinstance(repeats, list) else []
        sections.append(
            {
                "heading": "Suppressed repeats",
                "paragraphs": [
                    "Later signals for this service on this lane folded into "
                    "this card instead of opening competing ones. Nothing was "
                    "discarded — every fold is listed here and on the trail."
                ],
                "rows": _rows(
                    ("Repeats absorbed", record.suppressed_count),
                    ("Window", f"{suppression.get('window_hours', '—')} hours"),
                ),
                "bullets": [
                    f"{repeat.get('date', 'unknown date')}"
                    + (
                        f" — z {repeat['z_score']}"
                        if repeat.get("z_score") is not None
                        else ""
                    )
                    for repeat in repeats
                    if isinstance(repeat, dict)
                ],
            }
        )

    if savings:
        sections.append(
            {
                "heading": "Computed savings",
                "rows": _rows(
                    ("Daily excess", savings.get("daily_excess", "—")),
                    ("Cautious / month", savings.get("cautious_monthly", "—")),
                    ("Bold / month", savings.get("bold_monthly", "—")),
                    ("Method", savings.get("method")),
                ),
                "note": "Money figures are computed in Python, not generated.",
            }
        )

    if options:
        sections.append(
            {
                "heading": "Recommended options",
                "subsections": [
                    {
                        "heading": (
                            f"{option.get('stance', '—')} — {option.get('title', '—')}"
                        ),
                        "paragraphs": (
                            [str(option["description"])]
                            if option.get("description")
                            else []
                        ),
                        "rows": _rows(
                            ("Risk", option.get("risk", "—")),
                            (
                                "Estimated monthly saving",
                                # The stored option carries narrative only;
                                # the figure is the stance's own computed
                                # projection, never a number from the model.
                                option.get("estimated_monthly_saving")
                                or _stance_saving(savings, option.get("stance")),
                            ),
                            ("Rollback", option.get("rollback", "—")),
                        ),
                    }
                    for option in options
                    if isinstance(option, dict)
                ],
            }
        )

    if detail.get("escalation_reason"):
        sections.append(
            {
                "heading": "Escalation",
                "paragraphs": [str(detail["escalation_reason"])],
            }
        )

    if transcript:
        reviewers = transcript.get("reviewers")
        reviewers = reviewers if isinstance(reviewers, list) else []
        votes = transcript.get("votes") or {}
        sections.append(
            {
                "heading": (
                    "Review panel" if transcript.get("mode") == "panel" else "Debate"
                ),
                "rows": _rows(
                    ("Trigger", transcript.get("trigger")),
                    ("Original stance", transcript.get("original_preferred")),
                    ("Final stance", transcript.get("final_preferred")),
                    (
                        "Outcome",
                        "consensus"
                        if transcript.get("agreed")
                        else "OVERRULED — the stance was revised",
                    ),
                    (
                        "Votes",
                        ", ".join(f"{k} {v}" for k, v in votes.items()) if votes else "",
                    ),
                    ("Verdict", transcript.get("skeptic_rationale")),
                ),
                # One line per seat, abstentions included: a panel that lost a
                # reviewer must say so rather than quietly shrinking.
                "bullets": [
                    f"{reviewer.get('persona', 'reviewer')} "
                    f"({reviewer.get('model', 'unknown model')}): "
                    f"{reviewer.get('stance') or 'abstained'} — "
                    f"{reviewer.get('argument', '—')}"
                    for reviewer in reviewers
                    if isinstance(reviewer, dict)
                ],
            }
        )

    human: dict = {"heading": "Human decision"}
    if decision is not None:
        human["rows"] = _rows(
            ("Verdict", decision["verdict"]),
            ("By", record.decided_by),
            ("At", decision["created_at"]),
            ("Rationale", decision["rationale"] or "(none recorded)"),
        )
    elif record.state == DECIDABLE_STATE:
        human["bullets"] = ["Awaiting an operator decision."]
    else:
        human["bullets"] = [f"{record.state} (no rationale on record)."]
    sections.append(human)

    if execution:
        sections.append(
            {
                "heading": "Execution",
                "rows": _rows(
                    ("Mode", execution.get("mode", "—")),
                    ("Note", execution.get("note")),
                ),
            }
        )

    if record.history:
        sections.append(
            {
                "heading": "Lifecycle timeline",
                "paragraphs": [
                    "Append-only: every transition this card took, in order, "
                    "with the actor who caused it."
                ],
                "bullets": [
                    f"{entry.at} — **{entry.transition}**"
                    f"{f' by `{entry.actor}`' if entry.actor else ''}"
                    f"{f' — {entry.note}' if entry.note else ''}"
                    for entry in record.history
                ],
            }
        )

    if anomaly:
        sections.append(
            {
                "heading": "Verification",
                "bullets": verification_plan(anomaly, savings),
            }
        )

    return {
        "title": f"CloudSentinel Incident Report — action #{record.id}",
        "subtitle": record.title,
        "meta": _rows(
            ("State", record.state),
            ("Proposed", record.proposed_at),
            (
                "Decided",
                f"{record.decided_at} by `{record.decided_by}`"
                if record.decided_at
                else "",
            ),
            (
                "Executed",
                f"{record.executed_at} (simulated)" if record.executed_at else "",
            ),
            (
                "Repeats folded in",
                record.suppressed_count if record.suppressed_count else "",
            ),
        ),
        "sections": sections,
        "footer": [
            "CloudSentinel — the machine watches, the human decides.",
            "Execution is simulated by design; data is synthetic for the "
            "competition.",
        ],
    }


# --- renderers ---------------------------------------------------------------


def _markdown_section(section: dict, level: int) -> list[str]:
    lines = [f"{'#' * level} {section['heading']}", ""]
    if section.get("lead"):
        lines += [f"**{section['lead']}**", ""]
    for paragraph in section.get("paragraphs", []):
        lines += [paragraph, ""]
    for label, value in section.get("rows", []):
        lines.append(f"- **{label}:** {value}")
    if section.get("rows"):
        lines.append("")
    for bullet in section.get("bullets", []):
        lines.append(f"- {bullet}")
    if section.get("bullets"):
        lines.append("")
    if section.get("note"):
        lines += [f"> {section['note']}", ""]
    for subsection in section.get("subsections", []):
        lines += _markdown_section(subsection, level + 1)
    return lines


def _render_markdown(document: dict) -> str:
    lines = [f"# {document['title']}", "", f"**{document['subtitle']}**", ""]
    for label, value in document["meta"]:
        lines.append(f"- **{label}:** {value}")
    lines.append("")
    for section in document["sections"]:
        lines += _markdown_section(section, 2)
    lines += ["---", ""]
    for line in document["footer"]:
        lines += [f"*{line}*", ""]
    return "\n".join(lines)


# Self-contained on purpose: the export must render from a mail client, a
# ticket attachment or a file:// URL with no network and no stylesheet.
_REPORT_CSS = """
:root { color-scheme: light dark; }
body { margin: 0 auto; padding: 2.5rem 1.5rem; max-width: 46rem;
  font: 16px/1.6 ui-serif, Georgia, "Times New Roman", serif;
  color: #14181f; background: #fbfaf7; }
h1 { font-size: 1.6rem; margin: 0 0 .25rem; letter-spacing: -.01em; }
h2 { font-size: 1.1rem; margin: 2rem 0 .5rem; padding-bottom: .3rem;
  border-bottom: 1px solid #d8d3c8; text-transform: uppercase;
  letter-spacing: .08em; font-family: ui-sans-serif, system-ui, sans-serif; }
h3 { font-size: .95rem; margin: 1.2rem 0 .3rem;
  font-family: ui-sans-serif, system-ui, sans-serif; }
.subtitle { font-size: 1.15rem; margin: 0 0 1rem; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: .2rem .9rem;
  margin: .4rem 0 .8rem; }
dt { font-weight: 600; font-family: ui-sans-serif, system-ui, sans-serif;
  font-size: .85rem; opacity: .75; }
dd { margin: 0; overflow-wrap: anywhere; }
ul { margin: .4rem 0 .8rem; padding-left: 1.1rem; }
li { margin: .15rem 0; }
blockquote { margin: .6rem 0; padding: .4rem .9rem; border-left: 3px solid #b9b2a4;
  background: #f3f0e9; font-size: .92rem; }
footer { margin-top: 2.5rem; padding-top: .8rem; border-top: 1px solid #d8d3c8;
  font-size: .85rem; font-style: italic; opacity: .75; }
a { color: #1f5f8b; }
@media (prefers-color-scheme: dark) {
  body { color: #e7e3d9; background: #14181f; }
  h2, footer { border-color: #2c333d; }
  blockquote { background: #1b212a; border-color: #3a434f; }
  a { color: #7ec8f6; }
}
@media print { body { background: #fff; color: #000; } }
"""


def _html_value(value: str) -> str:
    """Escaped cell, with a bare URL turned into a link.

    Everything here can carry operator or model text, so escaping is not
    optional: the export is opened in a browser, and an unescaped rationale
    would be markup rather than words.
    """
    escaped = html.escape(value)
    if value.startswith(("https://", "http://")) and " " not in value:
        return f'<a href="{escaped}" rel="noopener noreferrer">{escaped}</a>'
    # the Markdown emphasis the shared document carries is decoration there
    # and noise here; strip the delimiters rather than print them raw
    return escaped.replace("**", "").replace("`", "")


def _html_section(section: dict, level: int) -> list[str]:
    tag = f"h{min(level, 6)}"
    out = [f"<{tag}>{html.escape(section['heading'])}</{tag}>"]
    if section.get("lead"):
        out.append(f"<p class=\"subtitle\">{html.escape(section['lead'])}</p>")
    for paragraph in section.get("paragraphs", []):
        out.append(f"<p>{html.escape(paragraph)}</p>")
    if section.get("rows"):
        out.append("<dl>")
        for label, value in section["rows"]:
            out.append(f"<dt>{html.escape(label)}</dt><dd>{_html_value(value)}</dd>")
        out.append("</dl>")
    if section.get("bullets"):
        out.append("<ul>")
        out += [f"<li>{_html_value(bullet)}</li>" for bullet in section["bullets"]]
        out.append("</ul>")
    if section.get("note"):
        out.append(f"<blockquote>{html.escape(section['note'])}</blockquote>")
    for subsection in section.get("subsections", []):
        out += _html_section(subsection, level + 1)
    return out


def _render_html(document: dict) -> str:
    body = [
        f"<h1>{html.escape(document['title'])}</h1>",
        f"<p class=\"subtitle\">{html.escape(document['subtitle'])}</p>",
        "<dl>",
    ]
    for label, value in document["meta"]:
        body.append(f"<dt>{html.escape(label)}</dt><dd>{_html_value(value)}</dd>")
    body.append("</dl>")
    for section in document["sections"]:
        body += _html_section(section, 2)
    body.append("<footer>")
    body += [f"<p>{html.escape(line)}</p>" for line in document["footer"]]
    body.append("</footer>")
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(document['title'])}</title>"
        f"<style>{_REPORT_CSS}</style></head><body>\n"
        + "\n".join(body)
        + "\n</body></html>\n"
    )


def _compose_report(
    conn: sqlite3.Connection, action_id: int, fmt: str = "md"
) -> str | None:
    """Load one action and render its incident report; None when it is gone.

    The single report-building path: the download endpoint serves it and
    the webhook dispatch ships it, so both always tell the same story.
    """
    row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    if row is None:
        return None
    decision = conn.execute(
        "SELECT verdict, rationale, created_at FROM decisions "
        "WHERE action_id = ? ORDER BY id DESC LIMIT 1",
        (action_id,),
    ).fetchone()
    event_kind = None
    if row["event_id"] is not None:
        event = conn.execute(
            "SELECT kind FROM events WHERE id = ?", (row["event_id"],)
        ).fetchone()
        event_kind = event["kind"] if event else None
    record = _to_record(row, history.for_actions(conn, [action_id]).get(action_id))
    document = _build_report_document(record, decision, event_kind)
    return _render_html(document) if fmt == "html" else _render_markdown(document)


@router.get(
    "/{action_id}/report",
    responses={
        200: {
            "content": {"text/markdown": {}, "text/html": {}},
            "description": "Downloadable incident report (Markdown or HTML).",
        },
        404: {"description": "No action with this id exists."},
    },
)
def action_report(
    action_id: int = ACTION_ID_PATH,
    format: Literal["md", "html"] = Query(
        "md",
        description=(
            "Markdown for a repo, a ticket or the execute webhook; HTML for a "
            "browser or a mail thread. Same content either way."
        ),
    ),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> Response:
    """Export the whole incident as one shareable document.

    Read-only: the signal and its triage, the recommended options with
    computed savings, the review panel's transcript with every seat's
    argument, the human verdict and rationale, the repeats suppression
    folded in, the append-only lifecycle timeline and the (simulated)
    execution marker — one request, nothing mutated.
    """
    content = _compose_report(conn, action_id, format)
    if content is None:
        raise HTTPException(
            status_code=404, detail=f"action {action_id} does not exist"
        )
    suffix = "html" if format == "html" else "md"
    media = "text/html" if format == "html" else "text/markdown"
    filename = f"cloudsentinel-incident-{action_id}.{suffix}"
    return Response(
        content=content,
        media_type=f"{media}; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
