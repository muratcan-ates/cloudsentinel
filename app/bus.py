"""Agent bus — the chain's inter-agent traffic, persisted and streamed.

Every hop of the orchestration is an EVENT on a shared bus: the reflex
opens a scan, the analyst picks a signal up and hands its triage to the
recommender, the recommender requests a skeptic review, the skeptic
answers, the chronicler files its briefing, the operator decides. Agents
publish as they work (single-statement inserts, so mid-run reads see the
traffic immediately under WAL), and the dashboard's live feed panel
polls the cursor endpoint — plain polling by locked decision, no
sockets. The feed is the debate transcript's bigger sibling: not one
exchange, the WHOLE conversation, replayable after the fact.
"""

import json
import sqlite3

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app import db
from app.missions import MissionError, get_mission

router = APIRouter(prefix="/agents", tags=["agents"])

# Keep the feed bounded: pruning runs at each pulse start, far above what
# a demo produces, so "replay the whole session" stays true in practice.
FEED_KEEP_ROWS = 1000


def emit(
    conn: sqlite3.Connection,
    agent: str,
    kind: str,
    message: str,
    payload: dict | None = None,
) -> None:
    """Publish one event. Joins an open transaction, else autocommits."""
    conn.execute(
        "INSERT INTO agent_feed (agent, kind, message, payload_json) "
        "VALUES (?, ?, ?, ?)",
        (agent, kind, message, json.dumps(payload) if payload is not None else None),
    )


def prune(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM agent_feed WHERE id <= "
        "(SELECT coalesce(max(id), 0) FROM agent_feed) - ?",
        (FEED_KEEP_ROWS,),
    )


class FeedEvent(BaseModel):
    id: int
    at: str
    agent: str
    kind: str
    message: str


class FeedReport(BaseModel):
    last_id: int
    count: int
    events: list[FeedEvent]


@router.get("/feed")
def agent_feed(
    after: int = Query(0, ge=0, description="Cursor: return events with id > after."),
    limit: int = Query(100, ge=1, le=500),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> FeedReport:
    """Stream the agent bus incrementally (poll with the last_id cursor)."""
    rows = conn.execute(
        "SELECT id, created_at, agent, kind, message FROM agent_feed "
        "WHERE id > ? ORDER BY id LIMIT ?",
        (after, limit),
    ).fetchall()
    events = [
        FeedEvent(
            id=row["id"],
            at=row["created_at"],
            agent=row["agent"],
            kind=row["kind"],
            message=row["message"],
        )
        for row in rows
    ]
    return FeedReport(
        last_id=events[-1].id if events else after,
        count=len(events),
        events=events,
    )


class AgentInfo(BaseModel):
    name: str
    role: str
    backend: str
    trigger: str
    guardrails: list[str]


class AgentRosterReport(BaseModel):
    count: int
    debate_threshold: float | None
    agents: list[AgentInfo]


AGENT_ROSTER = (
    AgentInfo(
        name="reflex",
        role="Deterministic first responder — resolves the mission's detection settings and runs the scan, reporting its measured latency.",
        backend="pure Python",
        trigger="every scan and every pulse",
        guardrails=["no LLM", "mission YAML validated hard before use"],
    ),
    AgentInfo(
        name="analyst",
        role="Triage — classifies each cost anomaly with cited evidence rows and a self-assessed confidence.",
        backend="Gemini / fake / rule-based fallback",
        trigger="per persisted cost anomaly",
        guardrails=["evidence citations validated", "reflection at critical severity", "answers cached, fallbacks never"],
    ),
    AgentInfo(
        name="recommender",
        role="Remediation — drafts one cautious and one bold option with risk and rollback; consumes decision memory and discloses it.",
        backend="Gemini / fake / rule-based fallback",
        trigger="per analyzed anomaly",
        guardrails=["savings computed in Python", "±5% numeric post-check", "one open card per signal"],
    ),
    AgentInfo(
        name="skeptic",
        role="Adversarial review — challenges contested drafts and can overturn the stance, on the record.",
        backend="Gemini / fake",
        trigger="low confidence, disagreement, or BOLD on a critical signal (stakes-raised bar)",
        guardrails=["at most one call per decision", "transcript persisted"],
    ),
    AgentInfo(
        name="chronicler",
        role="Narration — turns each pulse run's computed facts into an operator briefing.",
        backend="Gemini / fake / rule-based fallback",
        trigger="once per pulse",
        guardrails=["never invents figures", "budget-charged", "cached by exact facts"],
    ),
    AgentInfo(
        name="operator",
        role="The human — approves, rejects and (simulated) executes; every verdict feeds decision memory.",
        backend="human-in-the-loop",
        trigger="decision inbox",
        guardrails=["nothing executes unapproved", "idempotent decisions", "rationale recorded"],
    ),
)


@router.get("")
def agent_roster() -> AgentRosterReport:
    """The agent team: roles, triggers and guardrails, from the code."""
    try:
        threshold = get_mission().escalation.confidence_debate_threshold
    except MissionError:
        threshold = None
    return AgentRosterReport(
        count=len(AGENT_ROSTER),
        debate_threshold=threshold,
        agents=list(AGENT_ROSTER),
    )
