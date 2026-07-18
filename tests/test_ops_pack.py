"""Tests for the ops pack (Sprint 3): pulse LLM budget, rate limiting,
request ids, the decision-ledger CSV export and the FOCUS export schema.
"""

import pytest
from fastapi.testclient import TestClient

import main as main_module
from app.pulse import DEFAULT_PULSE_LLM_BUDGET, pulse_llm_budget
from tests.test_analytics import run_chain
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# --- pulse llm budget ------------------------------------------------------------


def test_pulse_reports_its_llm_spend(client):
    body = client.post("/pulse").json()
    assert body["llm_budget"] == DEFAULT_PULSE_LLM_BUDGET
    assert body["llm_calls_used"] > 0
    assert body["budget_exhausted"] is False


def test_exhausted_budget_degrades_to_fallbacks_not_failures(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_PULSE_LLM_BUDGET", "0")
    response = client.post("/pulse")
    assert response.status_code == 200  # the chain answers, degraded
    body = response.json()
    assert body["budget_exhausted"] is True
    assert body["llm_calls_used"] == 0
    assert body["signals"] == 2  # detection is deterministic, unaffected
    # every filed COST proposal came from the rule-based fallback lane;
    # fraud-hold and budget cards never touch an LLM in the first place
    actions = client.get("/actions").json()["actions"]
    cost_cards = [
        a for a in actions
        if a["detail"].get("kind") not in ("fraud_hold", "budget_risk")
    ]
    assert cost_cards
    assert all(a["detail"]["source"] == "fallback" for a in cost_cards)


def test_pulse_budget_env_parsing(monkeypatch):
    for garbage in ("", "abc", "-1"):
        monkeypatch.setenv("SENTINEL_PULSE_LLM_BUDGET", garbage)
        assert pulse_llm_budget() == DEFAULT_PULSE_LLM_BUDGET
    monkeypatch.setenv("SENTINEL_PULSE_LLM_BUDGET", "3")
    assert pulse_llm_budget() == 3


# --- rate limiting ---------------------------------------------------------------


def test_pulse_rate_limit_returns_429_with_retry_after(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_PULSE_RATE_LIMIT_PER_MINUTE", "2")
    main_module._pulse_hits.clear()
    assert client.post("/pulse").status_code == 200
    assert client.post("/pulse").status_code == 200
    limited = client.post("/pulse")
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    main_module._pulse_hits.clear()  # leave no residue for other tests


def test_rate_limit_zero_disables(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_PULSE_RATE_LIMIT_PER_MINUTE", "0")
    main_module._pulse_hits.clear()
    for _ in range(4):
        assert client.post("/pulse").status_code == 200


def test_rate_limit_does_not_touch_cheap_endpoints(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_PULSE_RATE_LIMIT_PER_MINUTE", "1")
    main_module._pulse_hits.clear()
    for _ in range(5):
        assert client.get("/health").status_code == 200
    main_module._pulse_hits.clear()


# --- request ids -----------------------------------------------------------------


def test_every_response_carries_a_request_id(client):
    response = client.get("/health")
    assert response.headers["X-Request-ID"]


def test_caller_supplied_request_id_is_echoed(client):
    response = client.get("/health", headers={"X-Request-ID": "trace-me-42"})
    assert response.headers["X-Request-ID"] == "trace-me-42"


# --- decision ledger export ------------------------------------------------------


def test_decisions_export_streams_the_ledger(client):
    run_chain(client, service="compute", occurred_on="2026-07-01", verdict="approve")
    run_chain(client, service="storage", occurred_on="2026-07-02", verdict="reject")
    response = client.get("/decisions/export")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "decision_ledger.csv" in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    assert lines[0] == "id,action_id,service,verdict,rationale,decided_at"
    assert len(lines) == 3
    assert ",compute,approved," in lines[1]
    assert ",storage,rejected," in lines[2]


def test_decisions_export_empty_ledger_is_just_the_header(client):
    lines = client.get("/decisions/export").text.strip().splitlines()
    assert lines == ["id,action_id,service,verdict,rationale,decided_at"]


# --- FOCUS export schema ---------------------------------------------------------


def test_focus_schema_maps_the_summary(client):
    response = client.get("/costs/summary/export", params={"schema": "focus"})
    assert response.status_code == 200
    assert "cost_summary_focus.csv" in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    assert lines[0] == "ServiceName,BilledCost,BillingCurrency,ChargePeriodStart,ChargePeriodEnd"
    summary = client.get("/costs/summary").json()
    top = summary["services"][0]
    assert f"{top['service']},{top['total_cost']},{summary['currency']}" in lines[1]


def test_default_schema_is_unchanged(client):
    lines = client.get("/costs/summary/export").text.strip().splitlines()
    assert lines[0] == "service,total_cost,mean_daily_cost,min_daily_cost,max_daily_cost,share_of_total"


def test_unknown_schema_is_rejected(client):
    assert (
        client.get("/costs/summary/export", params={"schema": "bogus"}).status_code
        == 422
    )
