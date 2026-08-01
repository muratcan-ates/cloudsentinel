"""The opt-in watchdog — the sentinel monitoring instead of waiting.

Acceptance criteria:
- off by default: no env knob, no thread, no behavior change anywhere;
- the interval parser refuses garbage and clamps hot loops to the floor;
- a tick runs a REAL full pulse on its own connection (proposals land),
  never passes a mission, and reports its beat honestly;
- read-only showcase mode skips the beat (direct calls bypass the HTTP
  middleware, so the guard re-checks here);
- a failing tick records the error and leaves the watch alive;
- the lifespan starts the thread when configured and stops it on exit.
"""

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app import db
from app.watchdog import MIN_INTERVAL_SECONDS, Watchdog, watch_interval
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
