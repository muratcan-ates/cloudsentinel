"""Self-telemetry — CloudSentinel watches itself (the live-data organ).

Every HTTP request the app serves increments an in-memory counter keyed
by (organ, day), where the organ is the room the path belongs to (watch,
agents, decide, intel, security, fraud, brain, auth, ops, dashboard).
The buffer flushes to SQLite on read and every FLUSH_EVERY increments,
so the per-request overhead stays a dict bump behind a lock.

With SENTINEL_COSTS_SOURCE=self the cost lane reads this real usage
history instead of the mock fixture: detection, analytics, insights and
the agent chain all run over traffic the app genuinely served, in
requests/day units (dataset currency "req"). The statistics stay honest
— a lane needs MIN_HISTORY days of accumulated real history before the
detector will score it, and that history is earned, never fabricated.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter

from app import db
from app.models import TelemetryUsageReport

logger = logging.getLogger("cloudsentinel.telemetry")

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

TELEMETRY_ENV = "SENTINEL_SELF_TELEMETRY"  # "0" disables recording

# Path first-segment -> organ. Anything unknown (/, room pages, /static,
# probes) counts as dashboard traffic.
ORGAN_BY_SEGMENT = {
    "costs": "watch",
    "anomalies": "watch",
    "metrics": "watch",
    "reflex": "watch",
    "pulse": "agents",
    "agents": "agents",
    "actions": "decide",
    "decisions": "decide",
    "analytics": "intel",
    "security": "security",
    "fraud": "fraud",
    "insights": "brain",
    "routines": "brain",
    "runbooks": "brain",
    "auth": "auth",
    "ops": "ops",
}

FLUSH_EVERY = 50
# After a failed flush, hold off size-triggered retries so a broken DB
# degrades to in-memory counting instead of a per-request retry storm.
FLUSH_RETRY_COOLDOWN_SECONDS = 30.0

_buffer: dict[tuple[str, str], int] = {}
_pending = 0
_last_flush_failure = 0.0  # time.monotonic() of the last failed flush
_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get(TELEMETRY_ENV, "").strip() != "0"


def organ_for_path(path: str) -> str:
    segment = path.strip("/").split("/", 1)[0].lower()
    return ORGAN_BY_SEGMENT.get(segment, "dashboard")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def record(path: str) -> bool:
    """Count one served request; True when a flush is due.

    Pure in-memory increment — the middleware runs on the event loop,
    so the blocking SQLite flush is left to the caller (offloaded to
    the threadpool). Never raises: observability must not take down
    the request it observes.
    """
    global _pending
    try:
        key = (organ_for_path(path), _today())
        with _lock:
            _buffer[key] = _buffer.get(key, 0) + 1
            _pending += 1
            return _pending >= FLUSH_EVERY and (
                time.monotonic() - _last_flush_failure
                >= FLUSH_RETRY_COOLDOWN_SECONDS
            )
    except Exception:
        logger.warning("telemetry record failed", exc_info=True)
        return False


def flush() -> None:
    """Drain the buffer into the telemetry_usage table (additive upsert)."""
    global _pending
    with _lock:
        if not _buffer:
            return
        drained = dict(_buffer)
        _buffer.clear()
        _pending = 0
    try:
        conn = db.connect_ready()
        try:
            with db.writing(conn):
                for (service, day), hits in sorted(drained.items()):
                    conn.execute(
                        "INSERT INTO telemetry_usage (service, day, hits) "
                        "VALUES (?, ?, ?) "
                        "ON CONFLICT(service, day) DO UPDATE SET "
                        "hits = hits + excluded.hits",
                        (service, day, hits),
                    )
        finally:
            conn.close()
    except Exception:
        # Put the counts back so a transient DB hiccup loses nothing,
        # and start the retry cooldown.
        global _last_flush_failure
        with _lock:
            for key, hits in drained.items():
                _buffer[key] = _buffer.get(key, 0) + hits
            _pending += sum(drained.values())
            _last_flush_failure = time.monotonic()
        logger.warning("telemetry flush failed — counts rebuffered", exc_info=True)


def reset_buffer() -> None:
    """Forget unflushed counts and failure state (test isolation)."""
    global _pending, _last_flush_failure
    with _lock:
        _buffer.clear()
        _pending = 0
        _last_flush_failure = 0.0


def usage_dataset() -> dict:
    """The recorded usage in the cost-dataset contract.

    This is what the cost lane's loaders serve when the source is
    ``self``: the same {date, service, cost} record shape, with cost
    carrying real requests/day. Empty history yields an empty (but
    well-formed) dataset — the panels render, the detector waits for
    real days to accumulate.
    """
    flush()
    conn = db.connect_ready()
    try:
        rows = conn.execute(
            "SELECT service, day, hits FROM telemetry_usage "
            "ORDER BY day, service"
        ).fetchall()
    finally:
        conn.close()
    records = [
        {"date": row["day"], "service": row["service"], "cost": float(row["hits"])}
        for row in rows
    ]
    today = _today()  # same UTC day the records are keyed by
    dates = [r["date"] for r in records]
    return {
        "description": (
            "live self-telemetry — requests served per organ per day; "
            "history accumulates while the server runs"
        ),
        "currency": "req",
        "period": {
            "start": min(dates) if dates else today,
            "end": max(dates) if dates else today,
        },
        "daily_costs": records,
    }


@router.get("/usage")
def get_telemetry_usage() -> TelemetryUsageReport:
    """The app's own request history — the live dataset behind self mode."""
    return TelemetryUsageReport(**usage_dataset())
