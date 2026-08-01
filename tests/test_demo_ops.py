"""Tests for the demo-operations pack: date rebase, pulse persistence,
per-run budget override, env-gated demo reset, read-only showcase mode,
the extended health check and the JSON failure envelope.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import main as main_module
from app import db
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# --- date rebase -----------------------------------------------------------------


def test_rebase_shifts_every_lane_by_the_same_whole_weeks(client, monkeypatch):
    from app.detection import load_dataset
    from app.fraud import load_fraud_dataset
    from app.security import load_security_dataset

    frozen_end = date.fromisoformat(load_dataset()["period"]["end"])
    security_before = max(r["date"] for r in load_security_dataset()["daily_counts"])
    fraud_before = max(e["date"] for e in load_fraud_dataset()["events"])

    monkeypatch.setenv("SENTINEL_REBASE_DATES", "1")
    rebased = load_dataset()
    rebased_end = date.fromisoformat(rebased["period"]["end"])
    shift = (rebased_end - frozen_end).days
    assert shift % 7 == 0  # whole weeks: weekday alignment survives
    assert rebased_end.weekday() == frozen_end.weekday()
    yesterday = date.today() - timedelta(days=1)
    assert timedelta(0) <= yesterday - rebased_end < timedelta(days=7)

    # the other lanes move by the SAME delta — same-day correlations hold
    security_after = max(r["date"] for r in load_security_dataset()["daily_counts"])
    fraud_after = max(e["date"] for e in load_fraud_dataset()["events"])
    delta = timedelta(days=shift)
    assert date.fromisoformat(security_after) == date.fromisoformat(security_before) + delta
    assert date.fromisoformat(fraud_after) == date.fromisoformat(fraud_before) + delta


def test_rebase_off_by_default(client):
    from app.detection import demo_rebase_delta

    assert demo_rebase_delta() == timedelta(0)


# --- pulse persistence and per-run budget ----------------------------------------


def test_pulse_last_replays_the_most_recent_run(client):
    assert client.get("/pulse/last").status_code == 404
    ran = client.post("/pulse").json()
    last = client.get("/pulse/last").json()
    assert last["ran_at"]
    assert last["report"]["signals"] == ran["signals"]
    assert last["report"]["briefing"]["headline"] == ran["briefing"]["headline"]


def test_pulse_budget_query_param_overrides_for_one_run(client):
    dry = client.post("/pulse", params={"llm_budget": 0}).json()
    assert dry["llm_budget"] == 0
    assert dry["budget_exhausted"] is True
    assert dry["briefing"]["source"] == "fallback"
    # the override is per-run: the next pulse is back on the default cap
    normal = client.post("/pulse").json()
    assert normal["llm_budget"] > 0


# --- demo reset ------------------------------------------------------------------


def test_demo_reset_is_a_404_without_the_knob(client):
    assert client.post("/ops/demo-reset").status_code == 404


def test_demo_reset_clears_state_but_preserves_the_ai_ledger(client, monkeypatch):
    client.post("/pulse")
    conn = db.connect()
    try:
        usage_before = conn.execute("SELECT count(*) FROM ai_usage").fetchone()[0]
    finally:
        conn.close()
    assert usage_before > 0

    monkeypatch.setenv("SENTINEL_DEMO_RESET", "1")
    body = client.post("/ops/demo-reset", params={"seed": 1}).json()
    assert body["seeded_decisions"] == 6
    assert "preserved" in body["note"]

    conn = db.connect()
    try:
        for table in ("events", "actions", "idempotency", "pulse_log"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        decisions = conn.execute("SELECT count(*) FROM decisions").fetchone()[0]
        usage_after = conn.execute("SELECT count(*) FROM ai_usage").fetchone()[0]
    finally:
        conn.close()
    assert decisions == 6  # the seed, nothing else
    assert usage_after == usage_before  # quota history never rewritten

    # seeded verdicts feed decision memory exactly like real ones
    similar = client.get("/decisions/similar", params={"service": "compute"}).json()
    assert similar["count"] == 2
    assert any("migration window" in (d["rationale"] or "") for d in similar["decisions"])


# --- read-only showcase mode -----------------------------------------------------


def test_readonly_blocks_every_post_but_keeps_reads(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_READONLY", "1")
    assert client.post("/pulse").status_code == 403
    assert client.post("/anomalies/1/analyze").status_code == 403
    assert client.post("/actions/1/approve").status_code == 403
    body = client.post("/pulse").json()
    assert "read-only" in body["detail"]
    assert client.get("/anomalies").status_code == 200
    assert client.get("/health").json()["readonly"] is True


# --- health and failure envelope -------------------------------------------------


def test_health_reports_version_provider_and_mode(client):
    body = client.get("/health").json()
    assert body["version"] == app.version
    assert body["provider"] == "fake"  # conftest pins SENTINEL_FAKE_LLM=1
    assert body["readonly"] is False


def test_failures_answer_with_a_json_envelope(monkeypatch):
    import sqlite3

    def busy():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(main_module, "load_dataset", busy)
    with TestClient(app, raise_server_exceptions=False) as raw_client:
        response = raw_client.get("/costs/summary")
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "2"
        assert response.json() == {"detail": "database is busy — retry shortly"}

    def broken():
        raise ValueError("boom")

    monkeypatch.setattr(main_module, "load_dataset", broken)
    with TestClient(app, raise_server_exceptions=False) as raw_client:
        response = raw_client.get("/costs/summary")
        assert response.status_code == 500
        assert response.json() == {"detail": "internal server error"}


# --- preflight: the runbook, executable ------------------------------------------
#
# docs/DEMO_PREFLIGHT.md is a human checklist, and a human checklist run at
# midnight before a deadline is exactly where a stage goes on camera broken.
# These cases pin that the executable version reports what is actually true.


def _check(body, name):
    return next(check for check in body["checks"] if check["name"] == name)


def test_preflight_answers_the_whole_checklist_in_one_call(client):
    response = client.get("/ops/preflight")
    assert response.status_code == 200
    body = response.json()
    assert {check["name"] for check in body["checks"]} == {
        "dataset",
        "mission",
        "provider",
        "readonly",
        "watchdog",
        "last_pulse",
        "disk",
        "data_sources",
        "security_headers",
        "demo_reset",
    }
    # the default local stage is demoable: nothing that would break a take
    assert body["ok"] is True
    assert body["failures"] == 0


def test_preflight_reports_the_stage_it_is_actually_standing_on(client):
    body = client.get("/ops/preflight").json()
    dataset = _check(body, "dataset")
    assert dataset["status"] == "pass"
    assert "cost rows" in dataset["detail"]
    assert _check(body, "mission")["detail"] == "resolved to 'finops'"
    # the fake provider is the pass condition, not a compromise
    assert _check(body, "provider")["status"] == "pass"
    assert _check(body, "data_sources")["detail"] == "costs=mock, fraud=mock, security=mock"
    assert _check(body, "security_headers")["status"] == "pass"


def test_preflight_warns_about_posture_without_failing_the_flag(client, monkeypatch):
    """A live provider or open writes may be exactly what this box is for."""
    body = client.get("/ops/preflight").json()
    assert _check(body, "readonly")["status"] == "warn"  # local stage, writes open
    assert body["ok"] is True
    assert body["warnings"] >= 1

    monkeypatch.setenv("SENTINEL_READONLY", "1")
    body = client.get("/ops/preflight").json()
    readonly = _check(body, "readonly")
    assert readonly["status"] == "pass"
    assert "403" in readonly["detail"]


def test_preflight_notices_an_empty_decision_desk(client):
    """The 'panels look stale' symptom, caught before the camera rolls."""
    body = client.get("/ops/preflight").json()
    assert _check(body, "last_pulse")["status"] == "warn"
    assert "empty" in _check(body, "last_pulse")["detail"]

    conn = db.connect_ready()
    try:
        with db.writing(conn):
            conn.execute("INSERT INTO pulse_log (report_json) VALUES ('{}')")
    finally:
        conn.close()
    body = client.get("/ops/preflight").json()
    assert _check(body, "last_pulse")["status"] == "pass"
    assert "0 min ago" in _check(body, "last_pulse")["detail"]


def test_preflight_fails_the_flag_when_the_dataset_is_gone(client, monkeypatch):
    """A fail is reserved for what would visibly break the demo."""

    def _broken(*args, **kwargs):
        raise RuntimeError("mock data missing")

    monkeypatch.setattr("app.detection.load_dataset", _broken)
    body = client.get("/ops/preflight").json()
    assert body["ok"] is False
    assert body["failures"] == 1
    dataset = _check(body, "dataset")
    assert dataset["status"] == "fail"
    assert "mock data missing" in dataset["detail"]


def test_preflight_fails_when_the_disk_will_not_take_a_write(client, monkeypatch):
    """Nothing persists on a read-only disk — better to know before the take.

    The refusal is injected at the write itself rather than by repointing
    the database path, which would take the request's own connection down
    with it and prove nothing about the check.
    """

    def _refuse(*args, **kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr("app.ops.tempfile.NamedTemporaryFile", _refuse)
    body = client.get("/ops/preflight").json()
    assert body["ok"] is False
    disk = _check(body, "disk")
    assert disk["status"] == "fail"
    assert "not writable" in disk["detail"]
    assert "Read-only file system" in disk["detail"]


def test_preflight_fails_on_a_frozen_watch(client, monkeypatch):
    """The stale watch is a demo-breaker, not a posture note."""
    from tests.test_watchdog import _FrozenWatch

    monkeypatch.setenv("SENTINEL_WATCH_INTERVAL_SECONDS", "300")
    monkeypatch.setattr("app.watchdog._current", _FrozenWatch(age_seconds=3 * 3600))
    body = client.get("/ops/preflight").json()
    assert body["ok"] is False
    assert _check(body, "watchdog")["status"] == "fail"
