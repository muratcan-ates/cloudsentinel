"""/runbooks — curated remediation runbooks with keyword retrieval (RAG-lite),
and the loop that measures whether the suggestions were any good."""

import pytest
from fastapi.testclient import TestClient

from app import db
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_list_runbooks_returns_the_corpus(client):
    data = client.get("/runbooks").json()
    assert data["count"] == len(data["runbooks"])
    assert data["count"] >= 5
    ids = {runbook["id"] for runbook in data["runbooks"]}
    assert "idle-compute" in ids


def test_match_retrieves_the_relevant_runbook(client):
    data = client.get("/runbooks/match", params={"query": "ec2 cost spike"}).json()
    matched_ids = [match["runbook"]["id"] for match in data["matches"]]
    # 'ec2' and 'cost'/'spike' hit the compute and spend-spike runbooks.
    assert "spend-spike" in matched_ids or "idle-compute" in matched_ids
    assert all(match["score"] > 0 for match in data["matches"])


def test_match_on_irrelevant_query_returns_no_matches(client):
    data = client.get("/runbooks/match", params={"query": "zzz nothing here"}).json()
    assert data["matches"] == []


# --- the corpus keeps score -------------------------------------------------
#
# Suggesting a playbook is easy; knowing whether it was any good is the part
# that usually never happens. Every operator verdict is a judgement on the
# card that carried it, so the corpus can measure its own hit rate — and a
# runbook whose cards are consistently rejected should stop being offered
# first. The association is recomputed, never stored: matching is a pure
# function of the card's text, so a stored link could only drift from it.


def _decide(conn, service: str, verdict: str, title: str | None = None) -> None:
    """One decided card, the way the product records them."""
    with db.writing(conn):
        action_id = None
        if title is not None:
            cursor = conn.execute(
                "INSERT INTO actions (event_id, title, detail_json, state) "
                "VALUES (NULL, ?, '{\"category\": \"LIFECYCLE\"}', ?)",
                (title, "approved" if verdict == "approved" else "rejected"),
            )
            action_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO decisions "
            "(action_id, service, verdict, rationale, input_context_json) "
            "VALUES (?, ?, ?, 'test verdict', '{}')",
            (action_id, service, verdict),
        )


def test_effectiveness_starts_honest_about_knowing_nothing(client):
    body = client.get("/runbooks/effectiveness").json()
    assert body["decisions_considered"] == 0
    assert {score["runbook_id"] for score in body["scores"]} == {
        runbook["id"] for runbook in client.get("/runbooks").json()["runbooks"]
    }
    for score in body["scores"]:
        assert score["decided"] == 0
        assert score["approval_rate"] is None
        assert score["adjustment"] == 0
        assert "under the 3 needed" in score["basis"]


def test_a_repeatedly_rejected_runbook_earns_a_demotion(client):
    conn = db.connect_ready()
    try:
        for _ in range(4):
            _decide(conn, "storage", "rejected", "Apply a lifecycle policy to the bucket")
    finally:
        conn.close()

    scores = {s["runbook_id"]: s for s in client.get("/runbooks/effectiveness").json()["scores"]}
    growth = scores["storage-growth"]
    assert growth["decided"] == 4
    assert growth["rejected"] == 4
    assert growth["approval_rate"] == 0.0
    assert growth["adjustment"] == -1
    assert "rejected 4 of 4" in growth["basis"]


def test_a_repeatedly_approved_runbook_earns_a_promotion(client):
    conn = db.connect_ready()
    try:
        for _ in range(3):
            _decide(conn, "network", "approved", "Move hot paths behind a CDN")
    finally:
        conn.close()

    scores = {s["runbook_id"]: s for s in client.get("/runbooks/effectiveness").json()["scores"]}
    assert scores["egress"]["adjustment"] == 1
    assert scores["egress"]["approval_rate"] == 1.0


def test_two_verdicts_are_an_anecdote_not_a_signal(client):
    """Under MIN_OBSERVATIONS the ranking stays purely on relevance."""
    conn = db.connect_ready()
    try:
        for _ in range(2):
            _decide(conn, "storage", "rejected", "Apply a lifecycle policy to the bucket")
    finally:
        conn.close()

    scores = {s["runbook_id"]: s for s in client.get("/runbooks/effectiveness").json()["scores"]}
    assert scores["storage-growth"]["decided"] == 2
    assert scores["storage-growth"]["adjustment"] == 0
    assert scores["storage-growth"]["approval_rate"] is None


def test_the_record_reorders_the_next_suggestion(client):
    """The whole point: what operators rejected stops being offered first."""
    query = "storage bucket disk cost spike"
    before = client.get("/runbooks/match", params={"query": query}).json()["matches"]
    before_ids = [match["runbook"]["id"] for match in before]
    assert before_ids[0] == "storage-growth", "relevance puts it first to begin with"

    conn = db.connect_ready()
    try:
        for _ in range(4):
            _decide(conn, "storage", "rejected", "Apply a lifecycle policy to the bucket")
    finally:
        conn.close()

    after = client.get("/runbooks/match", params={"query": query}).json()["matches"]
    after_ids = [match["runbook"]["id"] for match in after]
    assert after_ids[0] != "storage-growth", "a rejected playbook must stop leading"
    assert "storage-growth" in after_ids, "demoted, not deleted — it still matches"

    demoted = next(m for m in after if m["runbook"]["id"] == "storage-growth")
    assert demoted["adjustment"] == -1
    assert demoted["keyword_score"] > demoted["score"]
    assert "rejected" in (demoted["why"] or "")


def test_a_match_always_shows_both_halves_of_its_score(client):
    """The ordering is never a black box: keyword relevance, then the record."""
    matches = client.get(
        "/runbooks/match", params={"query": "ec2 cost spike"}
    ).json()["matches"]
    assert matches
    for match in matches:
        assert match["score"] == match["keyword_score"] + match["adjustment"]
        assert match["score"] >= 1, "a genuine match is never ranked out of existence"


def test_the_brain_recommends_rewriting_a_playbook_operators_reject(client):
    """The system's advice about its own advice."""
    conn = db.connect_ready()
    try:
        for _ in range(4):
            _decide(conn, "storage", "rejected", "Apply a lifecycle policy to the bucket")
    finally:
        conn.close()

    recommendations = client.get("/insights").json()["recommendations"]
    runbook_advice = {
        r["focus"]: r for r in recommendations if r["focus"].startswith("runbook:")
    }
    assert "runbook:storage-growth" in runbook_advice, (
        "a consistently rejected playbook must surface in insights"
    )
    advice = runbook_advice["runbook:storage-growth"]
    assert "Rewrite or retire" in advice["action"]
    assert "rejected 4 of 4" in advice["evidence"]
