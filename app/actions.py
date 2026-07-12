"""Human-in-the-loop action lifecycle (Sprint 2, WP-5a).

State machine (docs/architecture.md, binding):

    proposed -> approved | rejected -> executed (simulated, WP-5b)

Approve/reject are the operator decisions; every transition is persisted
with timestamp and actor. Both POST endpoints honor an optional
``Idempotency-Key`` header: the key is claimed inside the same write
transaction as the state change, so a retried or double-clicked decision
replays the first response instead of failing or double-executing. Keys
are scoped to action id + verb, so the same client key on a different
action (or on approve vs reject) can never replay a foreign response.

Only successful decisions are stored for replay: a 404/409 rolls the
whole transaction back (claim included), which keeps error outcomes
deterministic without persisting them.
"""

import json
import math
import os
import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query

from app import db
from models import ActionDecisionRequest, ActionListReport, ActionRecord, ActionState

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
        cursor = conn.execute(
            "UPDATE actions SET state = 'rejected', "
            "decided_at = datetime('now'), decided_by = ? "
            "WHERE state = 'proposed' AND proposed_at < datetime('now', ?)",
            (TIMEOUT_ACTOR, cutoff_modifier),
        )
        return cursor.rowcount


def _to_record(row: sqlite3.Row) -> ActionRecord:
    return ActionRecord(
        id=row["id"],
        event_id=row["event_id"],
        title=row["title"],
        detail=json.loads(row["detail_json"]),
        state=row["state"],
        proposed_at=row["proposed_at"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
        executed_at=row["executed_at"],
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
    records = [_to_record(row) for row in rows]
    return ActionListReport(count=len(records), actions=records)


def _decide(
    conn: sqlite3.Connection,
    action_id: int,
    verdict: str,
    actor: str,
    idempotency_key: str | None,
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
        record = _to_record(
            conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        )
        if scoped_key is not None:
            db.store_idempotency_response(conn, scoped_key, record.model_dump_json())
    return record


@router.post("/{action_id}/approve", responses=DECISION_RESPONSES)
def approve_action(
    action_id: int = ACTION_ID_PATH,
    decision: ActionDecisionRequest | None = None,
    idempotency_key: str | None = Header(
        None, alias="Idempotency-Key", min_length=1, max_length=200
    ),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> ActionRecord:
    """Approve a proposed action; safe to retry with an Idempotency-Key."""
    actor = decision.actor if decision is not None else "operator"
    return _decide(conn, action_id, "approved", actor, idempotency_key)


@router.post("/{action_id}/reject", responses=DECISION_RESPONSES)
def reject_action(
    action_id: int = ACTION_ID_PATH,
    decision: ActionDecisionRequest | None = None,
    idempotency_key: str | None = Header(
        None, alias="Idempotency-Key", min_length=1, max_length=200
    ),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> ActionRecord:
    """Reject a proposed action; safe to retry with an Idempotency-Key."""
    actor = decision.actor if decision is not None else "operator"
    return _decide(conn, action_id, "rejected", actor, idempotency_key)


@router.post("/{action_id}/execute", responses=EXECUTE_RESPONSES)
def execute_action(
    action_id: int = ACTION_ID_PATH,
    idempotency_key: str | None = Header(
        None, alias="Idempotency-Key", min_length=1, max_length=200
    ),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> ActionRecord:
    """Simulated execution of an approved action.

    No real infrastructure is ever touched: the transition is recorded
    with a SIMULATION marker in the action detail, which the dashboard
    surfaces as a badge. Safe to retry with an Idempotency-Key.
    """
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
        record = _to_record(
            conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        )
        if scoped_key is not None:
            db.store_idempotency_response(conn, scoped_key, record.model_dump_json())
    return record
