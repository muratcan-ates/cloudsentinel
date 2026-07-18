"""Demo operations — rehearsal hygiene, not product features.

One env-gated endpoint: ``POST /ops/demo-reset`` clears the decision
state (events, actions, decisions, idempotency claims, pulse log) so a
rehearsal or jury run starts from a clean stage. The AI ledgers —
``ai_usage`` and ``llm_cache`` — are deliberately preserved: quota
history is real spend and must never be rewritten.

``?seed=1`` follows the wipe with a handful of synthetic PAST operator
verdicts (clearly attributed to the demo seed), so the panels that feed
on decision memory — the memory fold, the funnel, the ROI table — do not
greet the jury empty right after a reset.

The whole module is inert unless ``SENTINEL_DEMO_RESET=1``: without the
knob the endpoint answers 404, indistinguishable from not existing.
"""

import logging
import os
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from app import db
from app.models import DemoResetReport

logger = logging.getLogger("cloudsentinel.ops")

router = APIRouter(prefix="/ops", tags=["ops"])

DEMO_RESET_ENV = "SENTINEL_DEMO_RESET"

# FK-safe wipe order: decisions reference actions, actions reference events.
CLEARED_TABLES = ("decisions", "actions", "events", "idempotency", "pulse_log")

SEED_ACTOR_NOTE = "seeded demo verdict"

# (service, verdict, rationale, hours_ago) — mixed outcomes with concrete
# rationales, so the memory digest and the calibration read like history.
SEED_DECISIONS = (
    ("compute", "approved", "idle capacity confirmed by the owning team — right-size approved", 160),
    ("compute", "rejected", "known migration window; spike expected to clear itself", 120),
    ("database", "approved", "read replica tier lowered after the maintenance check", 100),
    ("database", "rejected", "billing re-statement artifact — no workload change", 76),
    ("storage", "approved", "lifecycle rule applied to the archive bucket", 52),
    ("network", "rejected", "egress spike traced to a planned data export", 28),
)


def demo_reset_enabled() -> bool:
    return os.environ.get(DEMO_RESET_ENV, "").strip() == "1"


def _seed_decisions(conn: sqlite3.Connection) -> int:
    """Insert the synthetic verdict history (inside the caller's txn)."""
    for service, verdict, rationale, hours_ago in SEED_DECISIONS:
        conn.execute(
            "INSERT INTO decisions "
            "(action_id, service, verdict, rationale, input_context_json, created_at) "
            "VALUES (NULL, ?, ?, ?, ?, datetime('now', ?))",
            (
                service,
                verdict,
                rationale,
                f'{{"origin": "{SEED_ACTOR_NOTE}"}}',
                f"-{hours_ago} hours",
            ),
        )
    return len(SEED_DECISIONS)


@router.post(
    "/demo-reset",
    responses={404: {"description": "Demo reset is not enabled on this deployment."}},
)
def demo_reset(
    seed: bool = Query(
        False,
        description=(
            "Also seed a few synthetic past operator verdicts so the "
            "memory-fed panels stay populated after the wipe."
        ),
    ),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> DemoResetReport:
    """Clear the decision state for a clean rehearsal stage (env-gated)."""
    if not demo_reset_enabled():
        raise HTTPException(
            status_code=404, detail="demo reset is not enabled on this deployment"
        )
    with db.writing(conn):
        for table in CLEARED_TABLES:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 — fixed table list
        seeded = _seed_decisions(conn) if seed else 0
    logger.info("[OPS] demo reset — cleared %s, seeded %d", CLEARED_TABLES, seeded)
    return DemoResetReport(
        cleared=list(CLEARED_TABLES),
        seeded_decisions=seeded,
        note=(
            "ai_usage and llm_cache preserved — quota history is real spend "
            "and is never rewritten"
        ),
    )
