"""User routines — saved, named analysis playbooks.

A routine is an operator's saved ritual: a named, ordered list of
allow-listed read-only steps (e.g. "Morning check" = insights + pending
approvals + cost summary). Runs are user-triggered — the deploy target has
no background scheduler by design — so a routine is a one-click way to
re-run the same checks, not an autonomous agent. Scheduling arrives with the
background worker after the competition.
"""

import json
import sqlite3
import statistics

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel, Field

from app import db
from app.detection import load_daily_costs
from app.insights import compute_insights

router = APIRouter(prefix="/routines", tags=["routines"])

# Allow-listed, read-only steps. New steps are added here on purpose: a
# routine can never run an arbitrary or state-mutating action.
ALLOWED_STEPS = ("insights", "pending_actions", "cost_summary")

ROUTINE_ID_PATH = Path(ge=1, le=2**63 - 1, description="Routine id from GET /routines.")


class RoutineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=400)
    steps: list[str] = Field(min_length=1)


class Routine(BaseModel):
    id: int
    name: str
    description: str
    steps: list[str]
    created_at: str


class RoutineListReport(BaseModel):
    count: int
    routines: list[Routine]


class RoutineStepResult(BaseModel):
    step: str
    summary: dict


class RoutineRunReport(BaseModel):
    routine: str
    steps: list[RoutineStepResult]
    note: str


def _to_routine(row: sqlite3.Row) -> Routine:
    return Routine(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        steps=json.loads(row["steps_json"]),
        created_at=row["created_at"],
    )


def _run_step(conn: sqlite3.Connection, step: str) -> dict:
    if step == "insights":
        report = compute_insights(conn)
        return {
            "observations": report.observations,
            "predictions": [prediction.statement for prediction in report.predictions],
            "recommendations": len(report.recommendations),
        }
    if step == "pending_actions":
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM actions WHERE state = 'proposed'"
        ).fetchone()["c"]
        return {"pending": pending}
    if step == "cost_summary":
        records = load_daily_costs()
        costs = [float(record["cost"]) for record in records]
        days = len({str(record["date"]) for record in records})
        total = round(sum(costs), 2)
        return {
            "records": len(costs),
            "days": days,
            "total": total,
            "mean_per_day": round(total / days, 2) if days else 0.0,
        }
    # Unreachable: creation validates the allow-list, but stay explicit.
    return {"error": f"unknown step '{step}'"}


def _load_or_404(conn: sqlite3.Connection, routine_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM routines WHERE id = ?", (routine_id,)).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"routine {routine_id} does not exist"
        )
    return row


@router.post("", status_code=201)
def create_routine(
    body: RoutineCreate, conn: sqlite3.Connection = Depends(db.get_db)
) -> Routine:
    """Save a named routine. Steps are validated against the allow-list."""
    unknown = [step for step in body.steps if step not in ALLOWED_STEPS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"unknown step(s) {unknown}; allowed: {list(ALLOWED_STEPS)}",
        )
    with db.writing(conn):
        cursor = conn.execute(
            "INSERT INTO routines (name, description, steps_json) VALUES (?, ?, ?)",
            (body.name, body.description, json.dumps(body.steps)),
        )
        routine_id = cursor.lastrowid
    return _to_routine(_load_or_404(conn, routine_id))


@router.get("")
def list_routines(conn: sqlite3.Connection = Depends(db.get_db)) -> RoutineListReport:
    """List saved routines."""
    rows = conn.execute("SELECT * FROM routines ORDER BY id").fetchall()
    routines = [_to_routine(row) for row in rows]
    return RoutineListReport(count=len(routines), routines=routines)


def _daily_totals(records: list) -> list[float]:
    by_date: dict[str, float] = {}
    for record in records:
        key = str(record["date"])
        by_date[key] = by_date.get(key, 0.0) + float(record["cost"])
    return [by_date[key] for key in sorted(by_date)]


class RoutineSuggestion(BaseModel):
    name: str
    steps: list[str]
    rationale: str


class RoutineSuggestionsReport(BaseModel):
    count: int
    suggestions: list[RoutineSuggestion]
    note: str


@router.get("/suggestions")
def routine_suggestions(
    conn: sqlite3.Connection = Depends(db.get_db),
) -> RoutineSuggestionsReport:
    """Suggest routines grounded in the current state — the routines agent.

    Suggestions only; create one with POST /routines. Every suggested step
    stays within the read-only allow-list. Defined before /{routine_id} so
    the static path is not shadowed by the dynamic one.
    """
    suggestions = [
        RoutineSuggestion(
            name="Daily brief",
            steps=["insights", "pending_actions", "cost_summary"],
            rationale="A one-click daily read of the whole system.",
        )
    ]
    pending = conn.execute(
        "SELECT COUNT(*) AS c FROM actions WHERE state = 'proposed'"
    ).fetchone()["c"]
    if pending:
        suggestions.append(
            RoutineSuggestion(
                name="Inbox triage",
                steps=["pending_actions", "insights"],
                rationale=f"{pending} proposal(s) await a human decision.",
            )
        )
    totals = _daily_totals(load_daily_costs())
    if len(totals) >= 14:
        last = statistics.mean(totals[-7:])
        prior = statistics.mean(totals[-14:-7])
        if prior > 0 and last > prior:
            pct = round(100 * (last - prior) / prior, 1)
            suggestions.append(
                RoutineSuggestion(
                    name="Cost watch",
                    steps=["cost_summary", "insights"],
                    rationale=f"daily spend up {pct}% week over week.",
                )
            )
    return RoutineSuggestionsReport(
        count=len(suggestions),
        suggestions=suggestions,
        note="Suggestions only — create one with POST /routines.",
    )


@router.get("/{routine_id}", responses={404: {"description": "No such routine."}})
def get_routine(
    routine_id: int = ROUTINE_ID_PATH, conn: sqlite3.Connection = Depends(db.get_db)
) -> Routine:
    """Return one routine."""
    return _to_routine(_load_or_404(conn, routine_id))


@router.delete(
    "/{routine_id}",
    status_code=204,
    responses={404: {"description": "No such routine."}},
)
def delete_routine(
    routine_id: int = ROUTINE_ID_PATH, conn: sqlite3.Connection = Depends(db.get_db)
) -> Response:
    """Delete a routine."""
    _load_or_404(conn, routine_id)
    with db.writing(conn):
        conn.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
    return Response(status_code=204)


@router.post("/{routine_id}/run", responses={404: {"description": "No such routine."}})
def run_routine(
    routine_id: int = ROUTINE_ID_PATH, conn: sqlite3.Connection = Depends(db.get_db)
) -> RoutineRunReport:
    """Run a routine's steps in order and return each step's summary.

    Every step is read-only; running a routine never mutates decision state.
    """
    routine = _to_routine(_load_or_404(conn, routine_id))
    results = [
        RoutineStepResult(step=step, summary=_run_step(conn, step))
        for step in routine.steps
    ]
    return RoutineRunReport(
        routine=routine.name,
        steps=results,
        note="read-only routine run — no decision state was changed",
    )
