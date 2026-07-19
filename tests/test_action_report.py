"""GET /actions/{id}/report — a shareable Markdown incident report.

Read-only export composing the signal, recommended options, human decision
and rationale, and the simulated-execution marker into one document.
"""

import pytest
from fastapi.testclient import TestClient

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
