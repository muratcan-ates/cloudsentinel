"""The sentinel's own heartbeat — an opt-in, stdlib-only watchdog.

``SENTINEL_WATCH_INTERVAL_SECONDS`` > 0 starts one daemon thread from the
app lifespan that runs the full pulse chain on that cadence: the sentinel
MONITORS instead of waiting for a click. Off by default — the demo and
the test suite keep their request-triggered behavior; the knob makes the
deployment a standing watch.

Design notes (from the concurrency audit):
- one serial loop — a tick blocks until its pulse finishes, so overlap
  is impossible by construction;
- each tick opens its own connection (connections are per-use; WAL +
  BEGIN IMMEDIATE + busy timeout serialize against request writers) and
  closes it in ``finally``;
- ``run_pulse`` is called directly with explicit ``None`` arguments (its
  FastAPI ``Query`` defaults are sentinel objects, not values) and NEVER
  with a mission — the tick must not fight the dashboard quick-switch;
- read-only showcase mode skips ticks: direct calls bypass the HTTP
  middleware, so the guard is re-checked here;
- a failing tick logs and waits for the next beat — the watch survives
  its own bad days.

**The watch reports on itself.** On 1 August the deployed ``/pulse/last``
froze at 20:40 and nobody noticed for three hours: ``/health`` kept
answering 200 because the *process* was up — the *watch* was not. Liveness
is not the same question as "is the sentinel still watching". So the
watchdog publishes its own vitals (``health()``): last successful beat,
consecutive failures, the configured cadence, and **staleness** — no beat
within ``STALE_INTERVAL_MULTIPLIER`` × interval. ``/ready`` folds that in
as ``degraded`` and ``GET /ops/health/watch`` serves the detail.

Degraded, deliberately **not** 503: a slipped heartbeat must not take the
public showcase down with it. The readiness contract stays "can this
instance serve a request"; the watch answers a second question honestly
next to it.
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Literal

from fastapi import Response
from pydantic import BaseModel

from app import db
from app.logstream import log_tag
from app.models import ReadinessCheck, ReadinessStatus

logger = logging.getLogger("cloudsentinel.watchdog")

WATCH_ENV = "SENTINEL_WATCH_INTERVAL_SECONDS"
MIN_INTERVAL_SECONDS = 30.0  # a hot loop must not eat the LLM quota or the CPU
# A cold vitrine beats once shortly after boot instead of waiting a whole
# interval. Long enough to let startup and the platform's first healthcheck
# settle, short enough that a visitor arriving on a fresh instance still
# meets a populated decision desk.
WARMUP_DELAY_SECONDS = 3.0
# One missed beat is a slow pulse chain or a busy free-tier CPU; two in a
# row is a watch that has stopped watching. The multiplier is the line
# between patience and honesty.
STALE_INTERVAL_MULTIPLIER = 2.0


def watch_interval() -> float | None:
    """The configured cadence, or None when the watchdog stays off."""
    raw = os.environ.get(WATCH_ENV, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        logger.warning("ignoring invalid %s=%r; watchdog stays off", WATCH_ENV, raw)
        return None
    if value <= 0:
        return None
    if value < MIN_INTERVAL_SECONDS:
        logger.warning(
            "%s=%s is under the %.0fs floor; clamping",
            WATCH_ENV,
            raw,
            MIN_INTERVAL_SECONDS,
        )
        return MIN_INTERVAL_SECONDS
    return value


class Watchdog:
    """One daemon thread beating the pulse on a fixed cadence.

    The counters below are written by the watch thread and read by request
    threads answering ``/ready`` and ``/ops/health/watch``. They are plain
    attribute assignments on purpose: each read is a snapshot that may
    catch a beat mid-flight, which costs at worst a report that is one
    tick out of date — cheaper than a lock on the health path.
    """

    def __init__(self, interval: float):
        self.interval = interval
        self.ticks = 0
        self.last_error: str | None = None
        # Self-report vitals. `started_at` is the reference point for
        # staleness before the first beat lands: a watch that has been up
        # for 2× its interval without a single tick is already suspect.
        self.started_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.consecutive_errors = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self.started_at = _now()
        self._thread = threading.Thread(
            target=self._loop, name="sentinel-watchdog", daemon=True
        )
        self._thread.start()
        log_tag(logger, "[WATCHDOG]", state="started", interval_seconds=self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        log_tag(logger, "[WATCHDOG]", state="stopped", ticks=self.ticks)

    def _vitrine_is_cold(self) -> bool:
        """True when this database has never seen a pulse.

        An ephemeral deploy disk starts empty after every restart, so
        "cold" is the normal state of a free-tier instance that just woke
        up — not an error.
        """
        conn = None
        try:
            conn = db.connect_ready()
            return conn.execute("SELECT 1 FROM pulse_log LIMIT 1").fetchone() is None
        except Exception as error:  # a failed check must not kill the watch
            logger.warning("watchdog warm-up check failed: %s", error)
            return False
        finally:
            if conn is not None:
                conn.close()

    def _loop(self) -> None:
        # A cold vitrine fills itself right away: on an ephemeral disk every
        # restart wipes the database, and waiting a full interval would show
        # the first visitor an empty decision desk. A warm one keeps the
        # original wait-first cadence — boot stays fast, no duplicate beat.
        if self._vitrine_is_cold() and not self._stop.wait(WARMUP_DELAY_SECONDS):
            log_tag(logger, "[WATCHDOG]", state="warmup", reason="cold vitrine")
            self.tick()
        while not self._stop.wait(self.interval):
            self.tick()

    def tick(self) -> bool:
        """One beat: a full pulse on a fresh connection. True if it ran.

        The read-only showcase guard does NOT apply here by design: that
        middleware keeps STRANGERS' HTTP writes out, while the watchdog is
        the system's own heartbeat — a live deploy stays read-only to
        visitors yet keeps refreshing itself. An operator who wants a
        frozen showcase simply leaves the watch interval unset.
        """
        from app.pulse import run_pulse  # late import — pulse imports broadly

        conn = None
        try:
            conn = db.connect_ready()
            # Explicit Nones: the endpoint's Query defaults are FastAPI
            # sentinel objects. mission stays None by design — the tick
            # must never mutate the dashboard's quick-switch.
            report = run_pulse(threshold=None, llm_budget=None, mission=None, conn=conn)
            self.ticks += 1
            self.last_error = None
            self.consecutive_errors = 0
            self.last_success_at = _now()
            log_tag(
                logger,
                "[WATCHDOG]",
                tick=self.ticks,
                signals=report.signals,
                proposals_filed=report.proposals_filed,
                llm_calls_used=report.llm_calls_used,
            )
            return True
        except Exception as error:  # the watch survives its own bad days
            self.last_error = str(error)
            self.consecutive_errors += 1
            logger.warning("watchdog tick failed: %s", error, exc_info=True)
            return False
        finally:
            if conn is not None:
                conn.close()


# The instance the lifespan started, so any request thread can ask the
# watch how it is doing without threading app state through every caller.
# One process, one watch — set by start_from_env, cleared by stop_current.
_current: Watchdog | None = None


def current() -> Watchdog | None:
    """The watchdog this process started, or None when none is running."""
    return _current


def start_from_env() -> Watchdog | None:
    """Lifespan hook: a running watchdog when configured, else None."""
    global _current
    interval = watch_interval()
    if interval is None:
        _current = None
        return None
    watchdog = Watchdog(interval)
    watchdog.start()
    _current = watchdog
    return watchdog


def stop_current() -> None:
    """Lifespan teardown: stop the process watch and forget it."""
    global _current
    if _current is not None:
        _current.stop()
        _current = None


def forget_current() -> None:
    """Drop the registration without stopping anything (test hygiene)."""
    global _current
    _current = None


# --- the watch reports on itself ---------------------------------------------


WatchState = Literal["off", "starting", "healthy", "stale", "stopped"]


class WatchHealth(BaseModel):
    """What the standing watch says about its own condition.

    The question ``/health`` cannot answer: the process is up, but is the
    sentinel still watching? ``stale`` is the state that would have caught
    the 1 August freeze — a live process with a heartbeat three hours old.
    """

    # False when SENTINEL_WATCH_INTERVAL_SECONDS is unset: request-triggered
    # scanning is the documented default, not a fault.
    configured: bool
    running: bool
    state: WatchState
    # True only for states that mean the watch has stopped watching; the
    # caller decides what to do with it (/ready calls it `degraded`).
    degraded: bool
    interval_seconds: float | None = None
    # Seconds without a successful beat past which the watch is stale.
    stale_after_seconds: float | None = None
    ticks: int = 0
    consecutive_errors: int = 0
    last_error: str | None = None
    # Last successful beat by this process (UTC ISO), and its age.
    last_tick_at: str | None = None
    last_tick_age_seconds: float | None = None
    # Newest row in pulse_log — survives a restart, so it shows the gap a
    # fresh process inherited. None on a cold database or an unreadable one.
    last_pulse_at: str | None = None
    last_pulse_age_seconds: float | None = None
    detail: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_seconds(stamp: datetime | None) -> float | None:
    if stamp is None:
        return None
    return round((_now() - stamp).total_seconds(), 1)


def _last_pulse_at(conn: sqlite3.Connection | None = None) -> datetime | None:
    """Newest pulse_log timestamp, or None when there is none to read.

    A health probe must never fail because the thing it reports on is
    broken, so an unreadable database is simply "no answer" here — the
    database's own readiness check is what reports that.
    """
    owned = conn is None
    try:
        if conn is None:
            conn = db.connect_ready()
        row = conn.execute("SELECT max(created_at) AS latest FROM pulse_log").fetchone()
        latest = row["latest"] if row is not None else None
        if not latest:
            return None
        return datetime.strptime(latest, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except Exception as error:  # unreadable log, missing table, bad stamp
        logger.debug("watch health could not read the pulse log: %s", error)
        return None
    finally:
        if owned and conn is not None:
            conn.close()


def health(conn: sqlite3.Connection | None = None) -> WatchHealth:
    """The watch's own vitals — safe to call from any thread, never raises."""
    interval = watch_interval()
    watch = current()
    last_pulse = _last_pulse_at(conn)
    pulse_age = _age_seconds(last_pulse)

    if interval is None:
        return WatchHealth(
            configured=False,
            running=watch is not None and watch.running,
            state="off",
            degraded=False,
            last_pulse_at=last_pulse.isoformat() if last_pulse else None,
            last_pulse_age_seconds=pulse_age,
            detail=(
                f"standing watch off ({WATCH_ENV} unset) — scanning is "
                "request-triggered, which is the documented default"
            ),
        )

    stale_after = interval * STALE_INTERVAL_MULTIPLIER

    if watch is None or not watch.running:
        return WatchHealth(
            configured=True,
            running=False,
            state="stopped",
            degraded=True,
            interval_seconds=interval,
            stale_after_seconds=stale_after,
            ticks=watch.ticks if watch else 0,
            consecutive_errors=watch.consecutive_errors if watch else 0,
            last_error=watch.last_error if watch else None,
            last_pulse_at=last_pulse.isoformat() if last_pulse else None,
            last_pulse_age_seconds=pulse_age,
            detail=(
                f"a {interval:.0f}s watch is configured but no watch thread is "
                "running — this instance is not monitoring anything"
            ),
        )

    # Before the first beat, the watch is judged on how long it has been up:
    # a thread that has had two intervals and produced nothing is not new.
    reference = watch.last_success_at or watch.started_at
    age = _age_seconds(reference)
    beaten = watch.last_success_at is not None
    stale = age is not None and age > stale_after

    if stale:
        state: WatchState = "stale"
        detail = (
            f"no successful beat for {age:.0f}s — over the {stale_after:.0f}s "
            f"staleness line for a {interval:.0f}s watch"
        )
        if watch.consecutive_errors:
            detail += f"; {watch.consecutive_errors} consecutive failed tick(s)"
        if watch.last_error:
            detail += f"; last error: {watch.last_error}"
    elif beaten:
        state = "healthy"
        detail = f"last beat {age:.0f}s ago, inside the {stale_after:.0f}s line"
    else:
        state = "starting"
        detail = f"watch started {age:.0f}s ago; first beat not in yet"

    return WatchHealth(
        configured=True,
        running=True,
        state=state,
        degraded=stale,
        interval_seconds=interval,
        stale_after_seconds=stale_after,
        ticks=watch.ticks,
        consecutive_errors=watch.consecutive_errors,
        last_error=watch.last_error,
        last_tick_at=watch.last_success_at.isoformat() if beaten else None,
        last_tick_age_seconds=age if beaten else None,
        last_pulse_at=last_pulse.isoformat() if last_pulse else None,
        last_pulse_age_seconds=pulse_age,
        detail=detail,
    )


class ReadinessReport(ReadinessStatus):
    """Readiness plus the watch's own condition.

    ``checks`` keeps its meaning exactly — the dependencies a request
    touches, and the only thing that can produce a 503. The watch rides
    alongside in its own field, because a frozen sentinel behind a
    perfectly serviceable process is a different failure: real, worth
    saying out loud, and not a reason to pull the instance out of the
    load balancer.
    """

    # ok → serving and watching · degraded → serving, not watching ·
    # unready → a dependency is down (503).
    status: Literal["ok", "degraded", "unready"] = "ok"
    watch: WatchHealth


def readiness_report(
    checks: list[ReadinessCheck],
    version: str,
    provider: str,
    response: Response | None = None,
) -> ReadinessReport:
    """Assemble ``/ready``: dependency verdict, then the watch's own word.

    Sets 503 on the response only when a dependency check failed — never
    for staleness. The public showcase must survive a slipped heartbeat.
    """
    watch = health()
    ready = all(check.ok for check in checks)
    if not ready:
        if response is not None:
            response.status_code = 503
        status: Literal["ok", "degraded", "unready"] = "unready"
    elif watch.degraded:
        status = "degraded"
    else:
        status = "ok"
    return ReadinessReport(
        ready=ready,
        status=status,
        version=version,
        provider=provider,
        checks=checks,
        watch=watch,
    )
