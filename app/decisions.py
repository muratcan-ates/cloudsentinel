"""Decision memory retrieval (Sprint 2, WP-6).

Operator verdicts recorded by the HITL endpoints become retrievable
context: plain SQL by service, newest first — deliberately no
embeddings (locked decision). The Recommender injects these rows into
its frozen ``decision_memory`` prompt slot so repeated anomaly patterns
meet an agent that remembers how the humans decided last time.
"""

import csv
import io
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app import db
from app.models import DecisionListReport, DecisionRecord, DecisionSearchReport

router = APIRouter(tags=["memory"])


@router.get(
    "/decisions/export",
    response_class=Response,
    responses={
        200: {
            "content": {"text/csv": {}},
            "description": "CSV download of the decision ledger.",
        }
    },
)
def export_decisions_csv(
    conn: sqlite3.Connection = Depends(db.get_db),
) -> Response:
    """Download the decision ledger as CSV — the audit trail, portable."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["id", "action_id", "service", "verdict", "rationale", "decided_at"])
    for row in conn.execute(
        "SELECT id, action_id, service, verdict, rationale, created_at "
        "FROM decisions ORDER BY id"
    ):
        writer.writerow(
            [
                row["id"],
                row["action_id"],
                row["service"],
                row["verdict"],
                row["rationale"] or "",
                row["created_at"],
            ]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=decision_ledger.csv"},
    )


@router.get("/decisions")
def search_decisions(
    q: str | None = Query(
        None, min_length=1, max_length=200,
        description="Substring match over the recorded rationales.",
    ),
    verdict: Literal["approved", "rejected"] | None = Query(None),
    service: str | None = Query(None, min_length=1),
    limit: int = Query(50, ge=1, le=500),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> DecisionSearchReport:
    """Search the decision ledger — why did we decide what, and when.

    Plain SQL filters, newest first: the institutional memory becomes
    retrievable for humans the same way it already is for the agents.
    """
    clauses, params = [], []
    if q is not None:
        clauses.append("rationale LIKE ? ESCAPE '\\'")
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
    if verdict is not None:
        clauses.append("verdict = ?")
        params.append(verdict)
    if service is not None:
        clauses.append("service = ? COLLATE NOCASE")
        params.append(service.strip())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT id, action_id, service, verdict, rationale, created_at "
        f"FROM decisions {where} ORDER BY id DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return DecisionSearchReport(
        count=len(rows),
        filters={"q": q, "verdict": verdict, "service": service},
        decisions=[
            DecisionRecord(
                id=row["id"],
                action_id=row["action_id"],
                service=row["service"],
                verdict=row["verdict"],
                rationale=row["rationale"],
                decided_at=row["created_at"],
            )
            for row in rows
        ],
    )


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
