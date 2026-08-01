"""GET /actions/{id}/report — one shareable incident report, two renderings.

Read-only export composing the signal and its triage, the recommended
options with computed savings, the review panel's transcript, the human
decision and rationale, any suppressed repeats, the append-only lifecycle
timeline and the simulated-execution marker into one document —
``?format=md`` for a repo or a ticket, ``?format=html`` for a browser.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import db
from main import app
from tests.test_analytics import run_chain


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_incident_report_renders_the_full_timeline(client):
    body = run_chain(client, service="ec2", occurred_on="2026-07-12", verdict="approve")
    action_id = body["action_id"]

    response = client.get(f"/actions/{action_id}/report")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert f"incident-{action_id}" in response.headers.get("content-disposition", "")
    markdown = response.text
    assert markdown.startswith("# CloudSentinel Incident Report")
    assert "ec2" in markdown
    assert "## Recommended options" in markdown
    assert "## Human decision" in markdown
    assert "approved" in markdown
    # Blast-radius tier + framework reference from the enrichment module.
    assert "## Triage" in markdown
    assert "Blast radius" in markdown
    assert "FinOps Framework" in markdown
    # Post-action verification plan (detect-to-resolution).
    assert "## Verification" in markdown
    assert "Re-measure" in markdown
    # A curated runbook is cited (RAG-lite wired into the report).
    assert "## Suggested runbook" in markdown
    # Honesty carried into the artifact, not just the UI.
    assert "simulated by design" in markdown


def test_incident_report_for_an_undecided_action(client):
    body = run_chain(client, service="rds", occurred_on="2026-07-12", verdict=None)
    action_id = body["action_id"]

    markdown = client.get(f"/actions/{action_id}/report").text

    assert "## Human decision" in markdown
    assert "Awaiting an operator decision" in markdown


def test_incident_report_404_for_unknown_action(client):
    assert client.get("/actions/999999/report").status_code == 404


# --- the fuller record -------------------------------------------------------


def test_the_report_carries_the_panel_transcript_and_the_timeline(client):
    body = run_chain(client, service="ec2", occurred_on="2026-07-12", verdict="approve")
    markdown = client.get(f"/actions/{body['action_id']}/report").text

    # every seat argues on the record, abstentions included
    assert "## Review panel" in markdown or "## Debate" in markdown
    assert "Original stance" in markdown and "Final stance" in markdown
    # append-only lifecycle, in order, with the actor who caused each step
    assert "## Lifecycle timeline" in markdown
    assert "**filed**" in markdown and "**approved**" in markdown
    assert "agent:recommender" in markdown


def test_the_report_names_the_repeats_it_absorbed(client):
    body = run_chain(client, service="ec2", occurred_on="2026-07-12", verdict=None)
    action_id = body["action_id"]
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        detail = json.loads(row["detail_json"])
        detail["suppression"] = {
            "suppressed_count": 2,
            "window_hours": 24.0,
            "repeats": [
                {"date": "2026-07-13", "z_score": 3.1},
                {"date": "2026-07-14", "z_score": 2.7},
            ],
        }
        with db.writing(conn):
            conn.execute(
                "UPDATE actions SET detail_json = ? WHERE id = ?",
                (json.dumps(detail), action_id),
            )
    finally:
        conn.close()

    markdown = client.get(f"/actions/{action_id}/report").text

    assert "## Suppressed repeats" in markdown
    assert "2026-07-13" in markdown and "2026-07-14" in markdown
    assert "Repeats folded in" in markdown


def test_options_carry_their_computed_saving(client):
    """The figure is the stance's own Python projection, not a model number."""
    body = run_chain(client, service="ec2", occurred_on="2026-07-12", verdict=None)
    markdown = client.get(f"/actions/{body['action_id']}/report").text
    cautious = str(body["savings"]["cautious_monthly"])
    assert f"**Estimated monthly saving:** {cautious}" in markdown


# --- the HTML rendering ------------------------------------------------------


def test_html_format_downloads_as_html(client):
    body = run_chain(client, service="ec2", occurred_on="2026-07-12", verdict="approve")
    action_id = body["action_id"]

    response = client.get(f"/actions/{action_id}/report", params={"format": "html"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert f"incident-{action_id}.html" in response.headers["content-disposition"]
    assert response.text.startswith("<!doctype html>")
    assert "CloudSentinel Incident Report" in response.text


def test_html_and_markdown_tell_the_same_story(client):
    """One document model, two renderings — they cannot drift apart."""
    body = run_chain(client, service="ec2", occurred_on="2026-07-12", verdict="approve")
    action_id = body["action_id"]
    markdown = client.get(f"/actions/{action_id}/report").text
    page = client.get(f"/actions/{action_id}/report", params={"format": "html"}).text

    for heading in ("Signal", "Triage", "Human decision", "Lifecycle timeline"):
        assert f"## {heading}" in markdown
        assert f"<h2>{heading}</h2>" in page


def test_html_escapes_operator_and_model_text(client):
    """The export is opened in a browser: a rationale must be words, not markup."""
    body = run_chain(client, service="ec2", occurred_on="2026-07-12", verdict=None)
    assert (
        client.post(
            f"/actions/{body['action_id']}/reject",
            json={
                "actor": "operator",
                "rationale": "planned migration <script>alert(1)</script> & signed off",
            },
        ).status_code
        == 200
    )
    page = client.get(
        f"/actions/{body['action_id']}/report", params={"format": "html"}
    ).text

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&amp; signed off" in page


def test_html_is_self_contained(client):
    """No stylesheet, font or script fetch: it must render from a mail client."""
    body = run_chain(client, service="ec2", occurred_on="2026-07-12", verdict="approve")
    page = client.get(
        f"/actions/{body['action_id']}/report", params={"format": "html"}
    ).text

    assert "<link" not in page
    assert "<script" not in page
    assert "<style>" in page
    # the only outbound references are the framework citation links
    for marker in ('src="http', "@import", "url(http"):
        assert marker not in page


def test_an_unknown_format_is_rejected(client):
    body = run_chain(client, service="ec2", occurred_on="2026-07-12", verdict=None)
    response = client.get(
        f"/actions/{body['action_id']}/report", params={"format": "pdf"}
    )
    assert response.status_code == 422
