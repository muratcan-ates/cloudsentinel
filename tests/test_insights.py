"""GET /insights — deterministic history synthesis.

The brain reflects on its own past: observations, a run-rate prediction, and
improvement recommendations mined from operator behaviour. HITL-safe —
suggestions only, figures computed not generated.

The room now reads the whole record: what alert suppression folded away,
the decision-quality figures, the reflex rules drafted from settled
signatures, the runbook corpus's own hit rate and the audit chain's verdict
on all of it. Every read carries an evidence bar, so each test below comes
in a pair: the path where the bar is cleared and the observation is stated,
and the path where it is not and the report says so instead of guessing.
"""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db
from main import app
from tests.test_analytics import run_chain
from tests.test_recommender import seed_analyzed_event


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def observation_with(data: dict, needle: str) -> str | None:
    """The first observation containing ``needle``, or None."""
    return next((note for note in data["observations"] if needle in note), None)


def gap_with(data: dict, needle: str) -> str | None:
    return next((gap for gap in data["evidence_gaps"] if needle in gap), None)


def file_unanswered(client, *, service: str, occurred_on: str) -> dict:
    """File a proposal and leave it open, so the next repeat folds into it."""
    event_id = seed_analyzed_event(service=service, occurred_on=occurred_on)
    return client.post(f"/anomalies/{event_id}/recommend").json()


def approvals(client, service: str, days: tuple[str, ...]) -> None:
    for day in days:
        run_chain(client, service=service, occurred_on=day, verdict="approve")


# --- the original contract --------------------------------------------------


def test_insights_reflects_on_an_empty_system(client):
    data = client.get("/insights").json()
    assert data["decisions_considered"] == 0
    assert any("No operator decisions" in note for note in data["observations"])
    # The cost fixture spans enough days to project a run rate even with no
    # decisions yet.
    assert any(p["horizon"] == "next 30 days" for p in data["predictions"])
    assert "computed, not generated" in data["note"]


def test_insights_mines_recommendations_from_history(client):
    # Three unanimous approvals for one service → a reflex-playbook candidate;
    # a rejection for another → a threshold-review suggestion.
    for day in ("2026-07-05", "2026-07-06", "2026-07-08"):
        run_chain(client, service="ec2", occurred_on=day, verdict="approve")
    run_chain(client, service="rds", occurred_on="2026-07-09", verdict="reject")

    data = client.get("/insights").json()

    assert data["decisions_considered"] == 4
    focuses = {rec["focus"] for rec in data["recommendations"]}
    assert "ec2" in focuses  # pre-approved reflex playbook candidate
    assert "rds" in focuses  # rejection → review threshold
    assert any("approval rate" in note for note in data["observations"])


# --- the evidence bar itself ------------------------------------------------


def test_an_empty_system_names_every_gap_instead_of_asserting(client):
    """Nothing is claimed over zero rows — each silent read is named."""
    data = client.get("/insights").json()

    gaps = " | ".join(data["evidence_gaps"])
    for read in (
        "repeat-noise folding",
        "time to decision",
        "acceptance by severity",
        "agent uncertainty sources",
        "recurrence",
        "reflex drafts",
        "ledger integrity",
        "runbook effectiveness",
    ):
        assert read in gaps, f"{read} must be declared missing, not skipped"

    # The operator reading the room sees the gaps too, not just an API field.
    assert observation_with(data, "Not enough history yet to speak about")
    assert "evidence_gaps" in data["note"]


def test_gaps_close_as_the_record_fills(client):
    """A read moves out of the gap list exactly when it earns a sentence."""
    before = client.get("/insights").json()
    assert gap_with(before, "ledger integrity")

    run_chain(client, service="compute", occurred_on="2026-07-01", verdict="approve")

    after = client.get("/insights").json()
    assert gap_with(after, "ledger integrity") is None
    assert len(after["evidence_gaps"]) < len(before["evidence_gaps"])


# --- suppression: the noise that never reached a human ----------------------


def test_insights_report_what_suppression_folded_away(client):
    """Repeats folded into an open card are counted, not quietly dropped."""
    file_unanswered(client, service="network", occurred_on="2026-06-01")
    file_unanswered(client, service="network", occurred_on="2026-06-09")
    file_unanswered(client, service="network", occurred_on="2026-06-20")

    data = client.get("/insights").json()
    note = observation_with(data, "Alert suppression folded")

    assert note is not None
    # Two repeats folded into the one card that stayed open: three signals'
    # worth of inbox arrived as one.
    assert "folded 2 repeat signal(s) into 1 already-open card(s)" in note
    assert "3 cards' worth of signal reached the inbox as 1" in note
    assert gap_with(data, "repeat-noise folding") is None


def test_suppression_stays_silent_when_nothing_was_folded(client):
    """One card per signal is not a suppression story — say nothing, note it."""
    run_chain(client, service="compute", occurred_on="2026-07-01", verdict="approve")
    run_chain(client, service="storage", occurred_on="2026-07-02", verdict="approve")

    data = client.get("/insights").json()

    assert observation_with(data, "Alert suppression folded") is None
    assert gap_with(data, "repeat-noise folding") is not None


# --- decision quality: latency, acceptance slices, self-reported doubt ------


def test_time_to_decision_waits_for_five_measured_deliberations(client):
    """Four deliberations are anecdotes; the room says so and stays quiet."""
    approvals(client, "compute", ("2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04"))

    data = client.get("/insights").json()

    assert observation_with(data, "measured deliberations ended") is None
    assert "4 of 5 cards" in (gap_with(data, "time to decision") or "")


def test_time_to_decision_is_measured_from_the_trail(client):
    approvals(
        client,
        "compute",
        ("2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"),
    )

    data = client.get("/insights").json()
    note = observation_with(data, "measured deliberations ended")

    assert note is not None
    assert "Half of the 5 measured deliberations" in note
    assert "append-only trail" in note
    assert gap_with(data, "time to decision") is None


def test_a_verdict_without_a_measured_interval_does_not_count(client):
    """Seeded verdicts carry no card, so they cannot prop up a median."""
    approvals(client, "compute", ("2026-07-01", "2026-07-02", "2026-07-03"))

    conn = db.connect_ready()
    try:
        with db.writing(conn):
            for _ in range(4):
                conn.execute(
                    "INSERT INTO decisions (action_id, service, verdict, rationale, "
                    "input_context_json) VALUES (NULL, 'legacy', 'approved', 'seeded', "
                    "'{}')"
                )
    finally:
        conn.close()

    data = client.get("/insights").json()

    assert data["decisions_considered"] == 7
    # Seven verdicts, but only three of them were ever timed.
    assert observation_with(data, "measured deliberations ended") is None
    assert "3 of 5 cards" in (gap_with(data, "time to decision") or "")


def test_acceptance_by_severity_quotes_only_a_covered_slice(client):
    """Under the bar the slice is named as thin, never rounded into a rate."""
    approvals(client, "compute", ("2026-07-01", "2026-07-02"))

    thin = client.get("/insights").json()
    assert observation_with(thin, "severity the operators approved") is None
    assert "2 of 5 decided cards" in (gap_with(thin, "acceptance by severity") or "")

    approvals(client, "storage", ("2026-07-03", "2026-07-04", "2026-07-05"))

    data = client.get("/insights").json()
    note = observation_with(data, "severity the operators approved")

    assert note is not None
    # Every seeded card is a z=3.5 deviation, so the covered slice is critical.
    assert "On 'critical' severity the operators approved 5 of 5 cards (100.0%)" in note
    assert gap_with(data, "acceptance by severity") is None


def test_uncertainty_sources_are_tallied_from_the_traces(client):
    """What the agents said they were unsure ABOUT, read off the trace."""
    empty = client.get("/insights").json()
    assert observation_with(empty, "flagged their own uncertainty") is None
    assert "0 of 5 flags" in (gap_with(empty, "agent uncertainty sources") or "")

    # One chain files three flags — under the bar, so the room still says
    # nothing rather than crowning a "most common" source out of three.
    run_chain(client, service="compute", occurred_on="2026-07-01", verdict="approve")
    thin = client.get("/insights").json()
    assert observation_with(thin, "flagged their own uncertainty") is None
    assert "3 of 5 flags" in (gap_with(thin, "agent uncertainty sources") or "")

    run_chain(client, service="storage", occurred_on="2026-07-02", verdict="approve")

    data = client.get("/insights").json()
    note = observation_with(data, "flagged their own uncertainty")
    quality = client.get("/analytics/quality").json()
    loudest = quality["top_uncertainty_sources"][0]
    flags = sum(source["occurrences"] for source in quality["top_uncertainty_sources"])

    assert note is not None
    assert f"“{loudest['label']}”" in note
    assert f"{loudest['occurrences']} of {flags} flags" in note
    # The tally is the trace's, not the card's: every seat that spoke on a
    # card is counted, so two decisions clear a five-flag bar.
    assert flags > 2, "one flag per card would mean the trace was not read"
    assert gap_with(data, "agent uncertainty sources") is None


# --- recurrence, and the one extrapolation it is allowed to support ---------


def test_recurrence_needs_three_flagged_days(client):
    approvals(client, "compute", ("2026-07-01", "2026-07-02"))

    data = client.get("/insights").json()

    assert observation_with(data, "most recurrent source") is None
    assert "2 of 3 distinct flagged days" in (gap_with(data, "recurrence") or "")


def test_the_most_recurrent_source_is_named_with_its_span(client):
    file_unanswered(client, service="network", occurred_on="2026-06-01")
    file_unanswered(client, service="network", occurred_on="2026-06-09")
    file_unanswered(client, service="network", occurred_on="2026-06-20")

    data = client.get("/insights").json()
    note = observation_with(data, "most recurrent source")

    assert note is not None
    assert "network is the most recurrent source" in note
    assert "flagged on 3 separate days between 2026-06-01 and 2026-06-20" in note
    # The suppression figure and the recurrence figure agree, because both
    # read the same rows: one card, two repeats folded in.
    assert "opening 1 card(s) with 2 repeat(s) folded in" in note


def test_a_rate_is_extrapolated_only_from_a_long_enough_span(client):
    """Three flags inside one week is a burst, not a per-fortnight rate."""
    burst = ("2026-07-01", "2026-07-02", "2026-07-03")
    for day in burst:
        file_unanswered(client, service="database", occurred_on=day)

    data = client.get("/insights").json()
    assert observation_with(data, "database is the most recurrent source")
    assert not [p for p in data["predictions"] if p["horizon"] == "next 14 days"]

    # The same three flags spread across 20 days do support a flat rate.
    for day in ("2026-06-01", "2026-06-09", "2026-06-20"):
        file_unanswered(client, service="network", occurred_on=day)

    data = client.get("/insights").json()
    projection = next(
        p for p in data["predictions"] if p["horizon"] == "next 14 days"
    )

    assert "network flags on ≈2.1 more day(s)" in projection["statement"]
    assert "3 flagged days across the 20-day span" in projection["basis"]
    assert "a rate, not a cause" in projection["basis"]


def test_the_backlog_prediction_uses_the_measured_median(client):
    """No pending card, or no measured median, and the projection is absent."""
    approvals(
        client,
        "compute",
        ("2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"),
    )

    settled_only = client.get("/insights").json()
    assert not [
        p for p in settled_only["predictions"] if p["horizon"] == "current backlog"
    ]

    file_unanswered(client, service="storage", occurred_on="2026-07-06")

    data = client.get("/insights").json()
    backlog = next(p for p in data["predictions"] if p["horizon"] == "current backlog")

    assert "1 card(s) are waiting now" in backlog["statement"]
    assert "median of the measured filed→verdict intervals" in backlog["basis"]


# --- the rules the system drafts about itself ------------------------------


def test_reflex_drafts_and_contested_signatures_are_both_reported(client):
    """A signature the operators split on yields no rule — and is counted."""
    approvals(client, "compute", ("2026-07-01", "2026-07-02", "2026-07-03"))
    approvals(client, "storage", ("2026-07-01", "2026-07-02", "2026-07-03"))
    run_chain(client, service="storage", occurred_on="2026-07-04", verdict="reject")

    data = client.get("/insights").json()
    note = observation_with(data, "reflex rule draft(s) mined")

    assert note is not None
    assert "1 reflex rule draft(s) mined from settled anomaly signatures" in note
    assert "1 signature(s) stayed contested and produced no draft" in note

    drafted = [
        rec for rec in data["recommendations"] if rec["focus"].startswith("reflex:")
    ]
    assert len(drafted) == 1, "only the settled signature earns a draft"
    assert drafted[0]["focus"] == "reflex:compute/critical/spike"
    assert "Adopt or amend the drafted reflex rule" in drafted[0]["action"]
    assert "approved 3× on decision(s)" in drafted[0]["evidence"]
    # The draft the brain forwards is the one /reflex/suggestions publishes.
    published = client.get("/reflex/suggestions").json()
    assert published["contested_signatures"] == 1
    assert published["proposed_rules"][0]["signature"]["service"] == "compute"


def test_no_settled_signature_means_no_draft_sentence(client):
    approvals(client, "compute", ("2026-07-01", "2026-07-02"))

    data = client.get("/insights").json()

    assert observation_with(data, "reflex rule draft(s) mined") is None
    assert gap_with(data, "reflex drafts") is not None


# --- the runbook corpus's own record ---------------------------------------


def test_runbook_effectiveness_reports_its_leader(client):
    thin = client.get("/insights").json()
    assert observation_with(thin, "clear the 3-decided-card bar") is None
    assert gap_with(thin, "runbook effectiveness") is not None

    approvals(client, "compute", ("2026-07-01", "2026-07-02", "2026-07-03"))

    data = client.get("/insights").json()
    note = observation_with(data, "clear the 3-decided-card bar")
    scores = client.get("/runbooks/effectiveness").json()["scores"]
    leader = max(
        (score for score in scores if score["approval_rate"] is not None),
        key=lambda score: score["approval_rate"],
    )

    assert note is not None
    assert f"'{leader['title']}' leads at 100.0%" in note
    assert f"({leader['approved']} approved of {leader['decided']})" in note


# --- the audit chain: whether any of the above can be trusted --------------


def test_the_ledger_verdict_is_recomputed_and_reported(client):
    run_chain(client, service="compute", occurred_on="2026-07-01", verdict="approve")

    data = client.get("/insights").json()
    note = observation_with(data, "decision ledger verifies")
    audit = client.get("/audit/verify").json()

    assert note is not None
    assert f"{audit['verified']} of {audit['entries']} sealed entries" in note
    assert audit["head"][:12] in note


def test_a_tampered_record_is_announced_before_any_other_figure(client):
    """Rewrite a sealed verdict and the room stops vouching for its own data."""
    run_chain(client, service="compute", occurred_on="2026-07-01", verdict="approve")

    conn = db.connect_ready()
    try:
        with db.writing(conn):
            conn.execute("UPDATE decisions SET verdict = 'rejected' WHERE id = 1")
    finally:
        conn.close()

    data = client.get("/insights").json()
    note = observation_with(data, "decision ledger DOES NOT verify")

    assert note is not None
    assert "source_modified" in note
    advice = next(rec for rec in data["recommendations"] if rec["focus"] == "audit")
    assert "before trusting any figure" in advice["action"]
    assert "no longer matches the bytes that were sealed" in advice["evidence"]


# --- provenance: nothing here is generated ---------------------------------


def test_every_figure_traces_back_to_a_persisted_row(client):
    """Spot-check the arithmetic by hand against the tables it reads."""
    approvals(client, "compute", ("2026-07-01", "2026-07-02", "2026-07-03"))
    file_unanswered(client, service="storage", occurred_on="2026-07-04")
    file_unanswered(client, service="storage", occurred_on="2026-07-05")

    data = client.get("/insights").json()

    conn = db.connect_ready()
    try:
        conn.row_factory = sqlite3.Row
        decided = conn.execute("SELECT COUNT(*) AS c FROM decisions").fetchone()["c"]
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM actions WHERE state = 'proposed'"
        ).fetchone()["c"]
        folded = sum(
            json.loads(row["detail_json"]).get("suppression", {}).get(
                "suppressed_count", 0
            )
            for row in conn.execute("SELECT detail_json FROM actions")
        )
    finally:
        conn.close()

    assert data["decisions_considered"] == decided == 3
    assert pending == 1  # the second storage signal folded into the first card
    assert folded == 1
    assert f"folded {folded} repeat signal(s)" in (
        observation_with(data, "Alert suppression folded") or ""
    )


def test_the_report_survives_a_corrupt_detail_row(client):
    """A row the pipeline could not have written must not 500 the room."""
    run_chain(client, service="compute", occurred_on="2026-07-01", verdict="approve")

    conn = db.connect_ready()
    try:
        with db.writing(conn):
            conn.execute("UPDATE actions SET detail_json = 'not json' WHERE id = 1")
    finally:
        conn.close()

    response = client.get("/insights")

    assert response.status_code == 200
    assert response.json()["decisions_considered"] == 1
