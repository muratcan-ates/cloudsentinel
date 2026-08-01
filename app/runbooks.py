"""Curated remediation runbooks — grounded, retrievable playbooks (RAG-lite).

A small, curated corpus of known-good remediation runbooks with deterministic
keyword matching — retrieval without embeddings or an external service, so a
recommendation can cite a known procedure instead of free-generating one. The
corpus lives in code on purpose: it is auditable, offline, and bootcamp-safe.

**And it keeps score.** Suggesting a playbook is easy; knowing whether it was
any good is the part that usually never happens. Every operator verdict on
record is a judgement on the card that carried it, so the corpus can measure
its own hit rate: for each runbook, how many decided cards it matches, how
many of those the operator approved, and how many they rejected. A runbook
whose cards are consistently rejected drifts down the ranking; one whose
cards are consistently approved drifts up.

The link between a card and a runbook is not stored, it is *recomputed*:
matching is a pure deterministic function of the card's text, so persisting
the association would only be persisting a derived value that could drift
away from the code that produced it. Ask again and you get the same answer,
including for cards decided before this shipped.

The adjustment is bounded to a single rank step in either direction and needs
``MIN_OBSERVATIONS`` decided cards before it applies at all. Keyword relevance
still decides what a query matches; evidence only breaks ties among things
that already matched. No model, no learning rate — arithmetic an operator can
check by hand.
"""

import json
import sqlite3

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app import db

router = APIRouter(prefix="/runbooks", tags=["runbooks"])

# Below this many decided cards a hit rate is an anecdote, not a signal, and
# the ranking stays purely on keyword relevance.
MIN_OBSERVATIONS = 3


class Runbook(BaseModel):
    id: str
    title: str
    applies_to: list[str]
    steps: list[str]


RUNBOOKS: list[Runbook] = [
    Runbook(
        id="idle-compute",
        title="Idle or over-provisioned compute",
        applies_to=["compute", "ec2", "vm", "instance", "rightsizing", "cpu"],
        steps=[
            "Pull 14-day CPU/memory utilization for the resource.",
            "If sustained utilization is low, rightsize or schedule off-hours shutdown.",
            "Re-measure daily cost for 7 days and confirm it drops toward baseline.",
        ],
    ),
    Runbook(
        id="storage-growth",
        title="Unbounded storage growth",
        applies_to=["storage", "s3", "bucket", "disk", "volume", "snapshot"],
        steps=[
            "List largest prefixes/volumes and their last-access times.",
            "Apply a lifecycle policy (tiering + expiry) and delete orphaned snapshots.",
            "Verify the growth curve flattens on the next scan.",
        ],
    ),
    Runbook(
        id="spend-spike",
        title="Sudden spend spike",
        applies_to=["cost", "spike", "anomaly", "spend", "billing"],
        steps=[
            "Identify the top service/SKU driving the delta from baseline.",
            "Correlate with recent deploys, config changes and traffic.",
            "Set a budget alert at the new baseline and file the fix for approval.",
        ],
    ),
    Runbook(
        id="egress",
        title="Data-transfer / egress cost",
        applies_to=["network", "egress", "transfer", "bandwidth", "cdn"],
        steps=[
            "Break down transfer by cross-region vs internet egress.",
            "Move hot paths behind a CDN or co-locate chatty services.",
            "Confirm egress charges fall on the following billing day.",
        ],
    ),
    Runbook(
        id="suspicious-access",
        title="Suspicious access / security signal",
        applies_to=["security", "access", "auth", "fraud", "login", "credential"],
        steps=[
            "Review access logs for the flagged principal and source.",
            "Rotate exposed credentials and require MFA on the account.",
            "Confirm the anomalous signal clears after remediation.",
        ],
    ),
]


class RunbookListReport(BaseModel):
    count: int
    runbooks: list[Runbook]


class RunbookScore(BaseModel):
    """One runbook's record, and what that record earns it in the ranking."""

    runbook_id: str
    title: str
    # Decided cards whose text this runbook matches — the suggestions it
    # would have made, recomputed rather than remembered.
    decided: int
    approved: int
    rejected: int
    # None until MIN_OBSERVATIONS decided cards exist: a rate over two
    # decisions is an anecdote.
    approval_rate: float | None
    # -1, 0 or +1 rank steps. Bounded on purpose: evidence breaks ties, it
    # does not overrule what the operator actually asked for.
    adjustment: int
    basis: str


class RunbookEffectivenessReport(BaseModel):
    decisions_considered: int
    min_observations: int
    scores: list[RunbookScore]
    note: str


class RunbookMatch(BaseModel):
    runbook: Runbook
    score: int
    # The keyword score before the record was consulted, and the step the
    # record moved it — so a reader can always see both halves.
    keyword_score: int
    adjustment: int = 0
    why: str | None = None


class RunbookMatchReport(BaseModel):
    query: str
    matches: list[RunbookMatch]
    note: str


def keyword_score(runbook: Runbook, text: str) -> int:
    """How many of a runbook's keywords appear in the text. Case-insensitive."""
    lowered = text.lower()
    return sum(1 for keyword in runbook.applies_to if keyword in lowered)


def _decided_cards(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(searchable text, verdict) for every decision on record.

    The text is what the matcher would have seen: the service, the card's
    title, and the recommendation category. Seeded demo verdicts carry no
    action, so they contribute their service alone — thin, but honest, and
    the same matcher runs over it.
    """
    rows = conn.execute(
        "SELECT d.verdict AS verdict, d.service AS service, "
        "a.title AS title, a.detail_json AS detail_json "
        "FROM decisions d LEFT JOIN actions a ON a.id = d.action_id"
    ).fetchall()
    cards = []
    for row in rows:
        category = ""
        if row["detail_json"]:
            try:
                category = str(json.loads(row["detail_json"]).get("category") or "")
            except (json.JSONDecodeError, TypeError):
                category = ""
        text = " ".join(filter(None, (row["service"], row["title"], category)))
        cards.append((text, row["verdict"]))
    return cards


def _adjustment(approved: int, rejected: int) -> tuple[int, float | None, str]:
    """The rank step a record earns, and the sentence explaining it."""
    decided = approved + rejected
    if decided < MIN_OBSERVATIONS:
        return 0, None, (
            f"{decided} decided card(s) — under the {MIN_OBSERVATIONS} needed "
            "to move the ranking"
        )
    rate = approved / decided
    if rate >= 0.7:
        return 1, rate, (
            f"operators approved {approved} of {decided} cards this runbook "
            "matches — promoted one step"
        )
    if rate <= 0.3:
        return -1, rate, (
            f"operators rejected {rejected} of {decided} cards this runbook "
            "matches — demoted one step"
        )
    return 0, rate, (
        f"{approved} approved, {rejected} rejected of {decided} — no clear "
        "signal either way"
    )


def effectiveness(conn: sqlite3.Connection) -> RunbookEffectivenessReport:
    """Each runbook's own hit rate over the decisions on record."""
    cards = _decided_cards(conn)
    scores = []
    for runbook in RUNBOOKS:
        matched = [verdict for text, verdict in cards if keyword_score(runbook, text)]
        approved = sum(1 for verdict in matched if verdict == "approved")
        rejected = sum(1 for verdict in matched if verdict == "rejected")
        step, rate, basis = _adjustment(approved, rejected)
        scores.append(
            RunbookScore(
                runbook_id=runbook.id,
                title=runbook.title,
                decided=len(matched),
                approved=approved,
                rejected=rejected,
                approval_rate=None if rate is None else round(rate, 3),
                adjustment=step,
                basis=basis,
            )
        )
    scores.sort(key=lambda score: (-score.adjustment, score.runbook_id))
    return RunbookEffectivenessReport(
        decisions_considered=len(cards),
        min_observations=MIN_OBSERVATIONS,
        scores=scores,
        note=(
            "Hit rate is recomputed from persisted verdicts, never stored: "
            "matching is deterministic, so the association between a decided "
            "card and a runbook is a function, not a record that could drift. "
            "The adjustment is one rank step at most and no model is involved."
        ),
    )


def match_runbooks(
    query: str, limit: int = 3, conn: sqlite3.Connection | None = None
) -> list[tuple[Runbook, int, int, str | None]]:
    """Rank the corpus for a query: keyword relevance, then the record.

    Returns ``(runbook, final score, keyword score, why)``. Without a
    connection the record is not consulted at all and the ranking is exactly
    the keyword one — the shape every caller had before this existed.
    """
    scored = [
        (runbook, keyword_score(runbook, query))
        for runbook in RUNBOOKS
    ]
    hits = [(runbook, score) for runbook, score in scored if score > 0]

    steps: dict[str, tuple[int, str]] = {}
    if conn is not None:
        for record in effectiveness(conn).scores:
            if record.adjustment:
                steps[record.runbook_id] = (record.adjustment, record.basis)

    ranked = []
    for runbook, score in hits:
        step, why = steps.get(runbook.id, (0, None))
        # Never below 1: a demoted runbook that genuinely matches the query
        # still belongs in the list, just lower down.
        ranked.append((runbook, max(1, score + step), score, step, why))
    # Final score, then the record, then raw relevance, then id. The record
    # has to outrank relevance on a tie or a demotion could never actually
    # move anything: a runbook demoted INTO a tie would keep its old place on
    # the strength of the keyword score that got it demoted.
    ranked.sort(key=lambda row: (-row[1], -row[3], -row[2], row[0].id))
    return [(runbook, final, keywords, why) for runbook, final, keywords, _, why in ranked][
        :limit
    ]


@router.get("")
def list_runbooks() -> RunbookListReport:
    """The curated runbook corpus."""
    return RunbookListReport(count=len(RUNBOOKS), runbooks=RUNBOOKS)


@router.get("/match")
def match(
    query: str = Query(
        ..., min_length=1, description="Free text, e.g. 'ec2 cost spike'."
    ),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> RunbookMatchReport:
    """Retrieve the best-matching curated runbooks for a signal (keyword RAG-lite).

    Keyword relevance decides what matches; the corpus's own hit rate over
    past operator verdicts breaks ties among the things that already did.
    Each match carries both halves — the keyword score and the step the
    record moved it — so the ordering is never a black box.
    """
    matches = [
        RunbookMatch(
            runbook=runbook,
            score=score,
            keyword_score=keywords,
            adjustment=score - keywords,
            why=why,
        )
        for runbook, score, keywords, why in match_runbooks(query, conn=conn)
    ]
    return RunbookMatchReport(
        query=query,
        matches=matches,
        note=(
            "Curated runbooks, keyword-matched and then re-ranked by their own "
            "hit rate over recorded operator verdicts; no external retrieval, "
            "no model."
        ),
    )


@router.get("/effectiveness")
def runbook_effectiveness(
    conn: sqlite3.Connection = Depends(db.get_db),
) -> RunbookEffectivenessReport:
    """Does the suggestion actually help? The corpus measuring its own hit rate.

    For each runbook: how many decided cards its keywords match, how many of
    those the operator approved, how many they rejected, and the single rank
    step that record earns it. Recomputed from persisted verdicts on every
    call, so it covers cards decided long before the loop existed.
    """
    return effectiveness(conn)
