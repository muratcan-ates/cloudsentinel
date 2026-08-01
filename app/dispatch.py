"""Opt-in webhook dispatch — the decided incident leaves the building.

When ``SENTINEL_EXECUTE_WEBHOOK_URL`` is set, executing an approved action
ALSO delivers the incident — the decision, its computed savings and the
Markdown incident report — to the operator's own endpoint (Discord / Slack /
n8n style: any URL that accepts a JSON POST). Infrastructure mutation stays
SIMULATION by design; what really ships is the incident record itself.

Discipline (app/db.py's locked rule — never network inside an open
transaction): the execute transaction commits first, delivery runs after it,
and the outcome joins the action's audit detail in a second, small
transaction. A failed delivery never fails the execute — the state machine
must not depend on a webhook's mood; the honest failed-note is the record.
The URL itself may embed a secret token, so it is never logged or stored in
full: only its host appears anywhere.
"""

import json
import logging
import os
import sqlite3
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

from app import bus, db, netguard
from app.logstream import log_tag
from app.models import ActionRecord

logger = logging.getLogger("cloudsentinel.dispatch")

WEBHOOK_ENV = "SENTINEL_EXECUTE_WEBHOOK_URL"
DELIVERY_TIMEOUT_SECONDS = 10.0


def webhook_url() -> str | None:
    """The configured delivery endpoint, or None when dispatch stays off."""
    raw = os.environ.get(WEBHOOK_ENV, "").strip()
    return raw or None


def _target_host(url: str) -> str:
    """The loggable part of the URL — path and query may embed a secret."""
    try:
        return urlsplit(url).hostname or "unknown"
    except ValueError:
        return "unknown"


def build_payload(record: ActionRecord, report: str) -> dict:
    """Compact incident payload: the decision plus the shareable report.

    The headline figures come from the option the operator preferred; every
    lookup is defensive, so a legacy or partially populated action still
    ships a coherent payload.
    """
    detail = record.detail if isinstance(record.detail, dict) else {}
    anomaly = detail.get("anomaly") or {}
    stance = detail.get("preferred")
    options = detail.get("options") or []
    chosen = next(
        (
            option
            for option in options
            if isinstance(option, dict) and option.get("stance") == stance
        ),
        {},
    )
    return {
        "action_id": record.id,
        "title": record.title,
        "service": anomaly.get("service"),
        "stance": stance,
        "risk": chosen.get("risk"),
        "estimated_monthly_saving": chosen.get("estimated_monthly_saving"),
        "decided_by": record.decided_by,
        "executed_at": record.executed_at,
        "report": report,
    }


def dispatch_execution(
    conn: sqlite3.Connection,
    record: ActionRecord,
    compose_report: Callable[[], str],
) -> None:
    """Deliver one executed action to the configured webhook, if any.

    Call strictly AFTER the execute transaction has committed. No-op when
    the env knob is unset — today's behavior, byte-identical. Nothing here
    ever propagates an exception: the delivery outcome, honest either way,
    is written to the action's ``execution.dispatch`` audit detail instead.
    """
    url = webhook_url()
    if url is None:
        return
    host = _target_host(url)
    delivered, status = False, None
    try:
        payload = build_payload(record, compose_report())
        netguard.assert_safe_url(url)
        response = httpx.post(
            url,
            json=payload,
            timeout=DELIVERY_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
        status = response.status_code
        delivered = 200 <= status < 300
        note = "delivered" if delivered else f"failed: endpoint answered {status}"
    except Exception as error:  # delivery must never break the execute
        # exception text can echo the URL (token included) — class name only
        note = f"failed: {type(error).__name__}"
    try:
        _record_outcome(conn, record.id, delivered, status, host, note)
    except Exception:  # even a busy database must not fail the execute
        logger.warning(
            "action #%s: dispatch outcome could not be recorded",
            record.id,
            exc_info=True,
        )


def _record_outcome(
    conn: sqlite3.Connection,
    action_id: int,
    delivered: bool,
    status: int | None,
    host: str,
    note: str,
) -> None:
    """Second small transaction: the delivery verdict joins the audit trail."""
    with db.writing(conn):
        row = conn.execute(
            "SELECT detail_json FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
        if row is None:  # gone underfoot; nothing to annotate
            return
        detail = json.loads(row["detail_json"])
        execution = detail.get("execution")
        if not isinstance(execution, dict):
            execution = {}
            detail["execution"] = execution
        execution["dispatch"] = {
            "delivered": delivered,
            "status": status,
            "target": host,
            "note": note,
        }
        conn.execute(
            "UPDATE actions SET detail_json = ? WHERE id = ?",
            (json.dumps(detail), action_id),
        )
        bus.emit(
            conn,
            "operator",
            "dispatch",
            f"action #{action_id} incident report → {host} · {note}",
        )
    log_tag(
        logger,
        "[HITL]",
        action_id=action_id,
        transition="dispatched",
        delivered=delivered,
        status=status,
        target=host,
    )
