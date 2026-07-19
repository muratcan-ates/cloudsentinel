"""/runbooks — curated remediation runbooks with keyword retrieval (RAG-lite)."""

import pytest
from fastapi.testclient import TestClient

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
