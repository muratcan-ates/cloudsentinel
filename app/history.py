"""Append-only lifecycle trail per action (Sprint 3 decision-desk revamp).

Inherited from the Innova idea portal's evaluation history: every lifecycle
transition an action takes — filed, approved, rejected, executed, reopened,
expired — lands as one immutable row. ``decisions`` stays the operator-verdict
memory the recommender consults; this trail is the narrative the decision
desk renders as a per-card timeline. No update or delete path exists on
purpose.

"No update or delete path exists" is a claim about this code, not about
the database file underneath it, so every transition is also sealed into
the hash chain in ``app.ledger``: the trail carries the arithmetic to
prove it was not rewritten after the fact. Sealing rides inside the
caller's transaction, so a transition and its link commit together or
not at all.
"""

import sqlite3

from app import ledger
from app.models import ActionHistoryEntry


def record(
    conn: sqlite3.Connection,
    action_id: int,
    transition: str,
    actor: str | None = None,
    note: str | None = None,
) -> None:
    """Append one transition and seal it; joins an open txn, else autocommits.

    ``ledger.stamp`` seals every source row still outside the chain, not
    just this one, so a verdict written into ``decisions`` earlier in the
    same unit of work (the decide endpoint does exactly that) is sealed by
    the transition that records it — no caller has to remember to.
    """
    conn.execute(
        "INSERT INTO action_events (action_id, transition, actor, note) "
        "VALUES (?, ?, ?, ?)",
        (action_id, transition, actor, note),
    )
    ledger.stamp(conn)


# Transitions that end an operator's deliberation. 'expired' is absent on
# purpose: a proposal nobody answered measures absence, not thinking time.
_VERDICT_TRANSITIONS = ("approved", "rejected")


def deliberation_hours(
    conn: sqlite3.Connection, action_ids: list[int]
) -> dict[int, float]:
    """Hours from an action being filed to its FIRST human verdict.

    The trail is the only place this is recoverable per action: ``actions``
    keeps one ``decided_at``, which a reopen overwrites. Reads the first
    'filed' and the first verdict, so a card that was reopened and decided
    again still reports the original deliberation. Actions still open, or
    closed by the timeout, are simply absent from the result.
    """
    if not action_ids:
        return {}
    placeholders = ",".join("?" for _ in action_ids)
    verdicts = ",".join("?" for _ in _VERDICT_TRANSITIONS)
    rows = conn.execute(
        "SELECT action_id, ("
        f"julianday(min(CASE WHEN transition IN ({verdicts}) THEN created_at END)) "
        "- julianday(min(CASE WHEN transition = 'filed' THEN created_at END))"
        ") * 24.0 AS hours "
        f"FROM action_events WHERE action_id IN ({placeholders}) "  # noqa: S608
        "GROUP BY action_id",
        (*_VERDICT_TRANSITIONS, *action_ids),
    ).fetchall()
    # NULL hours means one end of the interval is missing — still open, or
    # closed by the timeout. Clamp at zero: same-second rows can land a
    # hair negative through julianday, and a negative deliberation is not
    # a measurement.
    return {
        row["action_id"]: max(0.0, round(row["hours"], 4))
        for row in rows
        if row["hours"] is not None
    }


def for_actions(
    conn: sqlite3.Connection, action_ids: list[int]
) -> dict[int, list[ActionHistoryEntry]]:
    """Load the trails for a set of actions in one query (inbox render path)."""
    if not action_ids:
        return {}
    placeholders = ",".join("?" for _ in action_ids)
    rows = conn.execute(
        "SELECT action_id, transition, actor, note, created_at "
        f"FROM action_events WHERE action_id IN ({placeholders}) ORDER BY id",
        action_ids,
    ).fetchall()
    trails: dict[int, list[ActionHistoryEntry]] = {}
    for row in rows:
        trails.setdefault(row["action_id"], []).append(
            ActionHistoryEntry(
                transition=row["transition"],
                actor=row["actor"],
                note=row["note"],
                at=row["created_at"],
            )
        )
    return trails
