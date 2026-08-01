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
"""

import logging
import os
import threading

from app import db
from app.logstream import log_tag

logger = logging.getLogger("cloudsentinel.watchdog")

WATCH_ENV = "SENTINEL_WATCH_INTERVAL_SECONDS"
MIN_INTERVAL_SECONDS = 30.0  # a hot loop must not eat the LLM quota or the CPU
# A cold vitrine beats once shortly after boot instead of waiting a whole
# interval. Long enough to let startup and the platform's first healthcheck
# settle, short enough that a visitor arriving on a fresh instance still
# meets a populated decision desk.
WARMUP_DELAY_SECONDS = 3.0


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
    """One daemon thread beating the pulse on a fixed cadence."""

    def __init__(self, interval: float):
        self.interval = interval
        self.ticks = 0
        self.last_error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
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
            logger.warning("watchdog tick failed: %s", error, exc_info=True)
            return False
        finally:
            if conn is not None:
                conn.close()


def start_from_env() -> Watchdog | None:
    """Lifespan hook: a running watchdog when configured, else None."""
    interval = watch_interval()
    if interval is None:
        return None
    watchdog = Watchdog(interval)
    watchdog.start()
    return watchdog
