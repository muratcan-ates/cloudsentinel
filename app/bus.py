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

Two properties keep that replay honest as the feed grows.

RETENTION. The bus is chatter, not evidence: every line here narrates
facts that live in ``events``, ``actions``, ``decisions`` and
``action_events``. That is what makes ``agent_feed`` the one table the
product deletes from — and why ``prune`` names it and nothing else. The
decision record is sealed row by row into the hash chain in
app/ledger.py, so a retention policy that reached those tables would not
tidy anything; it would surface as ``source_deleted`` the next time
``GET /audit/verify`` walked the chain, indistinguishable from tampering.
Feed rows age out on two axes because either alone leaks: a row cap lets
a quiet week keep week-old traffic, and a clock window lets a busy hour
grow without limit. Neither may take the last row — see ``prune``.

CURSOR. A page is the half-open id range ``(after, last_id]``, where
last_id is the id of the last row actually RETURNED — never the table's
maximum. That is what makes polling exact while agents are still
publishing: ids only ascend, so an event landing mid-read lands above
the page's last_id and the next poll serves it exactly once, no repeat
and no skip. An offset pager could promise neither here — retention
deletes from the HEAD of the feed, so every pruned row slides the rest
down under a client's offset and silently costs it a page boundary.
The page and its ``total`` come out of one statement for the same
reason: asked separately, a burst between the two queries would leave
the tally describing a feed the page never saw.
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
# A pulse emits on the order of twenty events, so a thousand rows covers
# many runs, and two days covers any sitting a jury will give it.
FEED_KEEP_ROWS = 1000
FEED_KEEP_HOURS = 48

# Page bounds, named because they are a contract rather than a magic
# number. The cap sits well below FEED_KEEP_ROWS on purpose: no single
# request can drain the retained feed, so `has_more` — not a short page —
# is what tells a client whether to poll again.
DEFAULT_PAGE_ROWS = 100
MAX_PAGE_ROWS = 500


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


def prune(
    conn: sqlite3.Connection,
    keep_rows: int = FEED_KEEP_ROWS,
    keep_hours: int = FEED_KEEP_HOURS,
) -> int:
    """Age out feed rows past either bound; returns how many were dropped.

    A row survives by clearing both bounds — among the newest
    ``keep_rows`` AND younger than ``keep_hours`` — save for the one
    exception below. The statement names ``agent_feed`` alone;
    ``decisions`` and ``action_events`` are the audit record and are
    sealed into the ledger's hash chain, so they are out of reach of
    retention by construction, not by convention.

    Survivors are picked with ``ORDER BY id DESC LIMIT`` rather than the
    old ``id <= max(id) - keep_rows``. The two agree today, because ids
    here happen to stay dense: inserts append and deletes only ever take
    the head. But the arithmetic form measures a SPAN of id values and
    reports it as a count of rows, so it is quietly leaning on a density
    nothing enforces — and if that ever lapses it under-retains with no
    symptom, since a feed that is merely shorter than intended looks
    exactly like a quiet one. Stating the policy directly costs nothing.

    The newest row is spared unconditionally, even when the clock has run
    out on it. An INTEGER PRIMARY KEY is the rowid, and SQLite hands out
    ``max(rowid) + 1`` — so emptying this table would restart ids at 1
    and every client holding a cursor from before would go permanently
    blind, its ``after`` sitting above everything the bus ever said
    again. One surviving row is what keeps ids strictly ascending, which
    is the assumption the whole cursor contract below rests on.

    Single statement, so it is safe in autocommit under the carve-out
    recorded in app/db.py — ``pulse`` calls it outside any transaction.
    """
    return conn.execute(
        "DELETE FROM agent_feed "
        "WHERE (id NOT IN (SELECT id FROM agent_feed ORDER BY id DESC LIMIT ?) "
        "OR created_at < datetime('now', ?)) "
        "AND id < (SELECT max(id) FROM agent_feed)",
        (keep_rows, f"-{keep_hours} hours"),
    ).rowcount


class FeedEvent(BaseModel):
    id: int
    at: str
    agent: str
    kind: str
    message: str


class FeedReport(BaseModel):
    last_id: int
    count: int
    # Everything still stored past the caller's cursor, this page
    # included, and whether reading it took more than this one page.
    total: int
    has_more: bool
    events: list[FeedEvent]


@router.get("/feed")
def agent_feed(
    after: int = Query(0, ge=0, description="Cursor: return events with id > after."),
    limit: int = Query(DEFAULT_PAGE_ROWS, ge=1, le=MAX_PAGE_ROWS),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> FeedReport:
    """Stream the agent bus incrementally (poll with the last_id cursor).

    ``total`` counts every stored event past ``after``, this page
    included, so a client that is behind is told so instead of having to
    infer it from a full-looking page. It rides along ON the page in a
    single statement: SQLite applies window functions before LIMIT, so
    the rows and the tally are read from one instant even while agents
    are publishing. A total that could disagree with its own page would
    be worse than no total at all.

    Counting is cheap only because retention keeps the table small —
    ``prune`` above is what pays for this honesty.
    """
    rows = conn.execute(
        "SELECT id, created_at, agent, kind, message, COUNT(*) OVER () AS matching "
        "FROM agent_feed WHERE id > ? ORDER BY id LIMIT ?",
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
    total = rows[0]["matching"] if rows else 0
    return FeedReport(
        # The cursor advances to the last row SERVED, never to the
        # table's max id: anything that landed past it is still owed.
        last_id=events[-1].id if events else after,
        count=len(events),
        total=total,
        has_more=total > len(events),
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
