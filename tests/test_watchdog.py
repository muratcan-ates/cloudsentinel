"""The opt-in watchdog — the sentinel monitoring instead of waiting.

Acceptance criteria:
- off by default: no env knob, no thread, no behavior change anywhere;
- the interval parser refuses garbage and clamps hot loops to the floor;
- a tick runs a REAL full pulse on its own connection (proposals land),
  never passes a mission, and reports its beat honestly;
- read-only showcase mode skips the beat (direct calls bypass the HTTP
  middleware, so the guard re-checks here);
- a failing tick records the error and leaves the watch alive;
- the lifespan starts the thread when configured and stops it on exit;
- the watch reports on itself — last beat, consecutive failures,
  staleness — and a frozen watch reads `degraded` on /ready, never 503.
"""

import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main as main_module
from app import db
from app.watchdog import MIN_INTERVAL_SECONDS, Watchdog, watch_interval
from app.watchdog import health as watchdog_health
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# --- interval parsing --------------------------------------------------------


def test_watchdog_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SENTINEL_WATCH_INTERVAL_SECONDS", raising=False)
    assert watch_interval() is None


def test_watch_interval_refuses_garbage_and_clamps_hot_loops(monkeypatch):
    monkeypatch.setenv("SENTINEL_WATCH_INTERVAL_SECONDS", "soon")
    assert watch_interval() is None
    monkeypatch.setenv("SENTINEL_WATCH_INTERVAL_SECONDS", "0")
    assert watch_interval() is None
    monkeypatch.setenv("SENTINEL_WATCH_INTERVAL_SECONDS", "5")
    assert watch_interval() == MIN_INTERVAL_SECONDS
    monkeypatch.setenv("SENTINEL_WATCH_INTERVAL_SECONDS", "300")
    assert watch_interval() == 300.0


# --- the beat ----------------------------------------------------------------


def test_tick_runs_a_real_pulse_and_files_proposals(client):
    watchdog = Watchdog(interval=3600)
    assert watchdog.tick() is True
    assert watchdog.ticks == 1
    assert watchdog.last_error is None
    # the beat did real work: the mock estate's signals became decidable cards
    inbox = client.get("/actions").json()
    assert inbox["actions"], "a tick must file proposals like a manual pulse"
    # and it never touched the quick-switch: the active mission is unchanged
    assert client.get("/anomalies").json()["mission"] == "finops"


def test_tick_beats_even_in_the_readonly_showcase(client, monkeypatch):
    """Read-only guards strangers' HTTP writes; the watchdog is the
    system's own heartbeat — the live showcase keeps refreshing itself."""
    monkeypatch.setenv("SENTINEL_READONLY", "1")
    watchdog = Watchdog(interval=3600)
    assert watchdog.tick() is True
    assert watchdog.ticks == 1
    # and strangers are still locked out over HTTP while the system lives
    assert client.post("/pulse").status_code == 403


def test_failing_tick_records_the_error_and_survives(client, monkeypatch):
    watchdog = Watchdog(interval=3600)
    monkeypatch.setattr(db, "connect_ready", _explode)
    assert watchdog.tick() is False
    assert watchdog.ticks == 0
    assert "boom" in (watchdog.last_error or "")


def _explode():
    raise RuntimeError("boom")


# --- lifespan wiring ---------------------------------------------------------


def test_lifespan_starts_and_stops_the_thread_when_configured(monkeypatch):
    monkeypatch.setenv("SENTINEL_WATCH_INTERVAL_SECONDS", "3600")
    with TestClient(app) as test_client:
        watchdog = test_client.app.state.watchdog
        assert watchdog is not None
        assert watchdog.running  # one interval away from its first beat
    assert not watchdog.running  # shutdown joined the thread


def test_lifespan_leaves_the_watch_off_when_unconfigured(client):
    assert client.app.state.watchdog is None


# --- the warm-up beat (a cold vitrine must not stay empty) -------------------


def test_cold_vitrine_beats_once_before_the_first_interval(client, monkeypatch):
    """A fresh instance fills its decision desk within seconds of boot.

    On an ephemeral deploy disk every restart wipes the database; waiting
    a whole interval would show the first visitor an empty desk.
    """
    monkeypatch.setattr("app.watchdog.WARMUP_DELAY_SECONDS", 0.01)
    watchdog = Watchdog(interval=3600)  # far longer than this test will wait
    assert watchdog._vitrine_is_cold() is True
    watchdog.start()
    try:
        deadline = time.monotonic() + 10
        while watchdog.ticks == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        watchdog.stop()
    assert watchdog.ticks == 1, "the cold vitrine must beat once, and only once"
    assert client.get("/actions").json()["actions"], "the desk is populated"


def test_a_warm_vitrine_keeps_the_wait_first_cadence(client, monkeypatch):
    """A database that already holds a pulse waits for its next beat —
    no duplicate work on a restart with a persistent disk."""
    monkeypatch.setattr("app.watchdog.WARMUP_DELAY_SECONDS", 0.01)
    assert Watchdog(interval=3600).tick() is True  # this one warms it
    watchdog = Watchdog(interval=3600)
    assert watchdog._vitrine_is_cold() is False
    watchdog.start()
    try:
        time.sleep(0.3)  # well past the warm-up delay, far short of the interval
    finally:
        watchdog.stop()
    assert watchdog.ticks == 0, "a warm vitrine must not beat early"


def test_warmup_check_survives_a_broken_database(monkeypatch):
    """The watch survives its own bad days: a failed check is not cold."""

    def explode(*args, **kwargs):
        raise sqlite3.OperationalError("disk is on fire")

    monkeypatch.setattr("app.db.connect_ready", explode)
    assert Watchdog(interval=3600)._vitrine_is_cold() is False


# --- the watch reports on itself ---------------------------------------------
#
# The 1 August incident: the deployed watch stopped beating at 20:40 and
# nothing said so for three hours, because /health only ever knew that the
# process was up. These cases pin the distinction — and pin that saying so
# must never cost the showcase a 503.


class _FrozenWatch:
    """A watch with a chosen history, without a real thread behind it."""

    def __init__(self, *, age_seconds=None, started_seconds_ago=0.0, errors=0):
        now = datetime.now(timezone.utc)
        self.interval = 300.0
        self.ticks = 0 if age_seconds is None else 7
        self.consecutive_errors = errors
        self.last_error = "pulse chain exploded" if errors else None
        self.started_at = now - timedelta(seconds=started_seconds_ago)
        self.last_success_at = (
            None if age_seconds is None else now - timedelta(seconds=age_seconds)
        )
        self.running = True

    def stop(self) -> None:
        """Stand-in for the real teardown the lifespan calls on shutdown."""
        self.running = False


@pytest.fixture
def configured_watch(monkeypatch):
    """A 300s standing watch is configured; the test supplies its state."""
    monkeypatch.setenv("SENTINEL_WATCH_INTERVAL_SECONDS", "300")

    def install(watch):
        monkeypatch.setattr("app.watchdog._current", watch)
        return watch

    return install


def test_health_calls_the_unconfigured_watch_off_not_broken(client):
    """Request-triggered scanning is the documented default, not a fault."""
    report = watchdog_health()
    assert report.configured is False
    assert report.state == "off"
    assert report.degraded is False
    assert report.interval_seconds is None
    assert "request-triggered" in report.detail


def test_health_reports_a_fresh_beat_as_healthy(client, configured_watch):
    configured_watch(_FrozenWatch(age_seconds=30))
    report = watchdog_health()
    assert report.state == "healthy"
    assert report.degraded is False
    assert report.interval_seconds == 300.0
    assert report.stale_after_seconds == 600.0  # 2x the interval
    assert report.last_tick_at is not None
    assert 25 <= (report.last_tick_age_seconds or 0) <= 60


def test_health_is_patient_with_a_watch_that_has_not_beaten_yet(
    client, configured_watch
):
    """A thread that started ten seconds ago is starting, not stale."""
    configured_watch(_FrozenWatch(age_seconds=None, started_seconds_ago=10))
    report = watchdog_health()
    assert report.state == "starting"
    assert report.degraded is False
    assert report.last_tick_at is None


def test_health_calls_a_frozen_watch_stale_and_says_why(client, configured_watch):
    """The 1 August case: process alive, watch three hours cold."""
    configured_watch(_FrozenWatch(age_seconds=3 * 3600, errors=4))
    report = watchdog_health()
    assert report.state == "stale"
    assert report.degraded is True
    assert report.consecutive_errors == 4
    assert "10800s" in report.detail  # the actual gap, not a vague word
    assert "4 consecutive failed tick(s)" in report.detail
    assert "pulse chain exploded" in report.detail


def test_health_reports_a_configured_watch_with_no_thread_as_stopped(
    client, configured_watch
):
    configured_watch(None)
    report = watchdog_health()
    assert report.configured is True
    assert report.running is False
    assert report.state == "stopped"
    assert report.degraded is True
    assert "not monitoring anything" in report.detail


def test_health_reads_the_pulse_log_so_a_restart_shows_the_inherited_gap(client):
    """last_pulse_at survives the process; last_tick_at does not.

    The row is written straight to the log rather than through POST /pulse:
    what is under test is that the watch reads the persisted stamp, not
    whether the whole agent chain ran today.
    """
    assert watchdog_health().last_pulse_at is None  # cold database
    conn = db.connect_ready()
    try:
        with db.writing(conn):
            conn.execute(
                "INSERT INTO pulse_log (report_json, created_at) "
                "VALUES ('{}', datetime('now', '-3 hours'))"
            )
    finally:
        conn.close()
    report = watchdog_health()
    assert report.last_pulse_at is not None
    # the inherited gap is visible, to the minute
    assert 3 * 3600 - 90 <= (report.last_pulse_age_seconds or 0) <= 3 * 3600 + 90


def test_health_never_raises_when_the_database_is_unreadable(client, monkeypatch):
    """A health probe that dies with the thing it reports on is useless."""

    def explode(*args, **kwargs):
        raise sqlite3.OperationalError("disk is on fire")

    monkeypatch.setattr("app.db.connect_ready", explode)
    report = watchdog_health()
    assert report.last_pulse_at is None
    assert report.state == "off"


def test_a_tick_resets_the_consecutive_error_run(client, monkeypatch):
    watch = Watchdog(interval=3600)
    monkeypatch.setattr(db, "connect_ready", _explode)
    assert watch.tick() is False
    assert watch.tick() is False
    assert watch.consecutive_errors == 2
    assert watch.last_success_at is None
    monkeypatch.undo()
    assert watch.tick() is True
    assert watch.consecutive_errors == 0
    assert watch.last_success_at is not None


# --- /ops/health/watch and the readiness verdict -----------------------------


def test_ops_health_watch_serves_the_detail(client, configured_watch):
    configured_watch(_FrozenWatch(age_seconds=3 * 3600, errors=1))
    response = client.get("/ops/health/watch")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "stale"
    assert body["degraded"] is True
    assert body["interval_seconds"] == 300.0
    assert body["consecutive_errors"] == 1


def test_ready_says_degraded_without_ever_answering_503(client, configured_watch):
    """The whole point: name the frozen watch, keep the vitrine alive.

    A 503 here would pull the public showcase out of rotation over a
    slipped heartbeat — the cure being worse than the disease.
    """
    configured_watch(_FrozenWatch(age_seconds=3 * 3600))
    response = client.get("/ready")
    assert response.status_code == 200, "a stale watch must not take the link down"
    body = response.json()
    assert body["status"] == "degraded"
    assert body["ready"] is True  # the dependencies are all fine
    assert body["watch"]["state"] == "stale"
    # and the dependency checks keep their own meaning
    assert {check["name"] for check in body["checks"]} == {
        "database",
        "missions",
        "dataset",
    }


def test_ready_is_ok_when_the_watch_is_off(client):
    body = client.get("/ready").json()
    assert body["status"] == "ok"
    assert body["watch"]["state"] == "off"


def test_ready_still_answers_unready_and_503_when_a_dependency_is_down(
    client, monkeypatch
):
    """Staleness is degraded; a missing dependency is still a hard 503."""
    monkeypatch.setattr(main_module, "load_dataset", _broken_dataset)
    response = client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unready"
    assert body["ready"] is False


def _broken_dataset():
    raise RuntimeError("mock data missing")
