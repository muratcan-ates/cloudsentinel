"""POST /insights/self-review — the system reviews itself and proposes.

HITL-sacred: it emits improvement proposals mined from its own history and
never applies any of them (`applied` is always empty).
"""

import pytest
from fastapi.testclient import TestClient

from main import app
from tests.test_analytics import run_chain


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_self_review_proposes_but_never_applies(client):
    # Unanimous approvals for one service, repeated rejections for another,
    # and one undecided proposal left pending.
    for day in ("2026-07-05", "2026-07-06", "2026-07-08"):
        run_chain(client, service="ec2", occurred_on=day, verdict="approve")
    run_chain(client, service="rds", occurred_on="2026-07-11", verdict="reject")
    run_chain(client, service="rds", occurred_on="2026-07-12", verdict="reject")
    run_chain(client, service="s3", occurred_on="2026-07-13", verdict=None)

    data = client.post("/insights/self-review").json()

    assert data["cycle"] == "self-review"
    assert data["applied"] == []  # nothing auto-applied — HITL-sacred
    assert data["proposals_considered"] == len(data["proposals"])
    areas = {proposal["area"] for proposal in data["proposals"]}
    assert "reflex" in areas  # ec2 all-approved → reflex-rule candidate
    assert "detection" in areas  # rds rejected twice → threshold review
    assert "backlog" in areas  # s3 pending → clear the inbox
    assert all(proposal["requires_human"] for proposal in data["proposals"])


def test_self_review_on_empty_system(client):
    data = client.post("/insights/self-review").json()
    assert data["applied"] == []
    assert data["proposals"] == []
