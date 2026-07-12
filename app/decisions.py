"""Decision memory retrieval (Sprint 2, WP-6).

Operator verdicts recorded by the HITL endpoints become retrievable
context: plain SQL by service, newest first — deliberately no
embeddings (locked decision). The Recommender injects these rows into
its frozen ``decision_memory`` prompt slot so repeated anomaly patterns
meet an agent that remembers how the humans decided last time.
"""

import sqlite3

from fastapi import APIRouter, Depends, Query

from app import db
from models import DecisionListReport, DecisionRecord

router = APIRouter(tags=["memory"])


@router.get("/decisions/similar")
def similar_decisions(
    service: str = Query(
        min_length=1,
        description="Service whose past operator decisions to retrieve.",
    ),
    limit: int = Query(5, ge=1, le=50),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> DecisionListReport:
    """Return the most recent operator verdicts for a service."""
    rows = conn.execute(
        "SELECT id, action_id, service, verdict, rationale, created_at "
        "FROM decisions WHERE service = ? COLLATE NOCASE "
        "ORDER BY id DESC LIMIT ?",
        (service.strip(), limit),
    ).fetchall()
    records = [
        DecisionRecord(
            id=row["id"],
            action_id=row["action_id"],
            service=row["service"],
            verdict=row["verdict"],
            rationale=row["rationale"],
            decided_at=row["created_at"],
        )
        for row in rows
    ]
    return DecisionListReport(
        service=service.strip(), count=len(records), decisions=records
    )
