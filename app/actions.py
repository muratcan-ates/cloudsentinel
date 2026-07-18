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
import logging
import math
import os
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response

from app import bus, db
from app.auth import UserOut, optional_user
from app.models import ActionDecisionRequest, ActionListReport, ActionRecord, ActionState

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
        expires_in_hours=_expires_in_hours(row),
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
        bus.emit(
            conn,
            "operator",
            "decision",
            f"action #{action_id} {verdict.upper()} by {actor}"
            + (f" — “{rationale}”" if rationale else "")
            + " · fed to decision memory",
        )
        record = _to_record(
            conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        )
        if scoped_key is not None:
            db.store_idempotency_response(conn, scoped_key, record.model_dump_json())
    # ids/enums only: the operator identity is PII on a log stream and is
    # already durably persisted in actions.decided_by for the audit trail.
    logger.info(
        "[HITL] %s",
        json.dumps(
            {"action_id": action_id, "transition": verdict},
            sort_keys=True,
        ),
    )
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
    (authoritative) rather than trusting the request body.
    """
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
    """Reject a proposed action; safe to retry with an Idempotency-Key.

    A valid session token makes the operator identity server-derived.
    """
    rationale = decision.rationale if decision is not None else None
    return _decide(
        conn, action_id, "rejected", _actor(user, decision), idempotency_key, rationale
    )


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
        bus.emit(
            conn,
            "operator",
            "execute",
            f"action #{action_id} executed — SIMULATION, no real infrastructure touched",
        )
        record = _to_record(
            conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        )
        if scoped_key is not None:
            db.store_idempotency_response(conn, scoped_key, record.model_dump_json())
    logger.info(
        "[HITL] %s",
        json.dumps(
            {"action_id": action_id, "transition": "executed", "mode": "SIMULATION"},
            sort_keys=True,
        ),
    )
    return record


def _render_report(record: ActionRecord, decision: sqlite3.Row | None) -> str:
    """Compose a shareable Markdown incident report from one action.

    Read-only and defensive: every field is optional, so a partially
    populated or legacy action still renders a coherent document.
    """
    detail = record.detail if isinstance(record.detail, dict) else {}
    anomaly = detail.get("anomaly") or {}
    savings = detail.get("savings") or {}
    options = detail.get("options") or []
    execution = detail.get("execution") or {}

    lines = [
        f"# CloudSentinel Incident Report — action #{record.id}",
        "",
        f"**{record.title}**",
        "",
        f"- **State:** {record.state}",
        f"- **Proposed:** {record.proposed_at}",
    ]
    if record.decided_at:
        lines.append(f"- **Decided:** {record.decided_at} by `{record.decided_by}`")
    if record.executed_at:
        lines.append(f"- **Executed:** {record.executed_at} (simulated)")
    lines.append("")

    if anomaly:
        cost = anomaly.get("cost", "—")
        base = anomaly.get("service_mean", "—")
        z = anomaly.get("z_score", "—")
        sev = anomaly.get("severity", "—")
        det = anomaly.get("detector", "—")
        lines += [
            "## Signal",
            "",
            f"- **Service:** {anomaly.get('service', '—')}",
            f"- **Date:** {anomaly.get('date', '—')}",
            f"- **Cost:** {cost} (baseline {base})",
            f"- **z-score:** {z} · **severity:** {sev} · **detector:** {det}",
            "",
        ]

    if savings:
        lines += [
            "## Computed savings",
            "",
            f"- Daily excess: {savings.get('daily_excess', '—')}",
            f"- Cautious / month: {savings.get('cautious_monthly', '—')}",
            f"- Bold / month: {savings.get('bold_monthly', '—')}",
        ]
        if savings.get("method"):
            lines.append(f"- Method: {savings['method']}")
        lines += ["", "> Money figures are computed in Python, not generated.", ""]

    if options:
        lines += ["## Recommended options", ""]
        for opt in options:
            if not isinstance(opt, dict):
                continue
            lines += [f"### {opt.get('stance', '—')} — {opt.get('title', '—')}", ""]
            if opt.get("description"):
                lines += [str(opt["description"]), ""]
            lines += [
                f"- Risk: {opt.get('risk', '—')}",
                f"- Estimated monthly saving: {opt.get('estimated_monthly_saving', '—')}",
                f"- Rollback: {opt.get('rollback', '—')}",
                "",
            ]

    if detail.get("escalation_reason"):
        lines += ["## Escalation", "", str(detail["escalation_reason"]), ""]

    lines += ["## Human decision", ""]
    if decision is not None:
        rationale = decision["rationale"] or "(none recorded)"
        lines += [
            f"- **Verdict:** {decision['verdict']}",
            f"- **By:** `{record.decided_by}` at {decision['created_at']}",
            f"- **Rationale:** {rationale}",
            "",
        ]
    elif record.state == DECIDABLE_STATE:
        lines += ["- Awaiting an operator decision.", ""]
    else:
        lines += [f"- {record.state} (no rationale on record).", ""]

    if execution:
        lines += ["## Execution", "", f"- Mode: **{execution.get('mode', '—')}**"]
        if execution.get("note"):
            lines.append(f"- {execution['note']}")
        lines.append("")

    lines += [
        "---",
        "",
        "*CloudSentinel — the machine watches, the human decides.*",
        "",
        "*Execution is simulated by design; data is synthetic for the "
        "competition.*",
        "",
    ]
    return "\n".join(lines)


@router.get(
    "/{action_id}/report",
    responses={404: {"description": "No action with this id exists."}},
)
def action_report(
    action_id: int = ACTION_ID_PATH,
    conn: sqlite3.Connection = Depends(db.get_db),
) -> Response:
    """Export a shareable Markdown incident report for one action.

    Read-only: composes the signal, the recommended options with computed
    savings, the human decision and rationale, and the (simulated) execution
    marker into one downloadable document. No state is mutated.
    """
    row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"action {action_id} does not exist"
        )
    decision = conn.execute(
        "SELECT verdict, rationale, created_at FROM decisions "
        "WHERE action_id = ? ORDER BY id DESC LIMIT 1",
        (action_id,),
    ).fetchone()
    markdown = _render_report(_to_record(row), decision)
    filename = f"cloudsentinel-incident-{action_id}.md"
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
