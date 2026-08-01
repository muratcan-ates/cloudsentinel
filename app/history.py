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
