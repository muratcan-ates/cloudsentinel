"""GET /insights — deterministic history synthesis.

The brain reflects on its own past: observations, a run-rate prediction, and
improvement recommendations mined from operator behaviour. HITL-safe —
suggestions only, figures computed not generated.
"""

import pytest
from fastapi.testclient import TestClient

from main import app
from tests.test_analytics import run_chain


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


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
