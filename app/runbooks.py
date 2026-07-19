"""Curated remediation runbooks — grounded, retrievable playbooks (RAG-lite).

A small, curated corpus of known-good remediation runbooks with deterministic
keyword matching — retrieval without embeddings or an external service, so a
recommendation can cite a known procedure instead of free-generating one. The
corpus lives in code on purpose: it is auditable, offline, and bootcamp-safe.
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/runbooks", tags=["runbooks"])


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


class RunbookMatch(BaseModel):
    runbook: Runbook
    score: int


class RunbookMatchReport(BaseModel):
    query: str
    matches: list[RunbookMatch]
    note: str


def match_runbooks(query: str, limit: int = 3) -> list[tuple[Runbook, int]]:
    """Score the corpus by how many of a runbook's keywords appear in the query."""
    text = query.lower()
    scored = [
        (runbook, sum(1 for keyword in runbook.applies_to if keyword in text))
        for runbook in RUNBOOKS
    ]
    hits = [(runbook, score) for runbook, score in scored if score > 0]
    hits.sort(key=lambda pair: (-pair[1], pair[0].id))
    return hits[:limit]


@router.get("")
def list_runbooks() -> RunbookListReport:
    """The curated runbook corpus."""
    return RunbookListReport(count=len(RUNBOOKS), runbooks=RUNBOOKS)


@router.get("/match")
def match(
    query: str = Query(
        ..., min_length=1, description="Free text, e.g. 'ec2 cost spike'."
    ),
) -> RunbookMatchReport:
    """Retrieve the best-matching curated runbooks for a signal (keyword RAG-lite)."""
    matches = [
        RunbookMatch(runbook=runbook, score=score)
        for runbook, score in match_runbooks(query)
    ]
    return RunbookMatchReport(
        query=query,
        matches=matches,
        note="Curated runbooks, keyword-matched; no external retrieval or model.",
    )
