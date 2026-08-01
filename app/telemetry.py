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

import json
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


# --- run receipts -----------------------------------------------------------
#
# Every pulse already leaves three separate records: the report it filed
# in pulse_log (signals, proposals, calls charged against the budget), the
# measured per-hop durations inside each card's orchestration trace, and
# the reflex latency the fast lane clocked. Nobody had put them on one
# piece of paper. A receipt does exactly that — what the run did, how many
# agent turns it took, how long those turns actually ran, and what it
# spent — assembled here on the READ side, so pulse.py stays untouched
# and running the watch costs nothing extra.

# Hops that are an agent taking a turn. 'memory' is a SQL read with no
# model behind it, so counting it as a turn would inflate the receipt.
AGENT_HOP_STEPS = ("analyst", "recommender", "skeptic", "panel")

RECEIPT_METHOD = (
    "assembled on read from pulse_log, the persisted orchestration traces "
    "and the reflex latency — measured figures only, nothing estimated"
)


def _trace_of(detail_json: str) -> list[dict]:
    try:
        trace = json.loads(detail_json).get("trace")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return []
    return trace if isinstance(trace, list) else []


def run_receipts(conn, limit: int = 10) -> list[dict]:
    """One receipt per recent pulse, newest first.

    Agent time is the sum of MEASURED hop durations, not the HTTP
    round trip: it is what the agents actually ran for, which is the
    figure that means something when the same run is compared across
    missions or providers. Cards filed before the trace carried
    durations contribute turns but no milliseconds, and the receipt
    says how many hops were unmeasured rather than guessing at them.
    """
    receipts = []
    for row in conn.execute(
        "SELECT id, report_json, created_at FROM pulse_log ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall():
        try:
            report = json.loads(row["report_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        chain = report.get("chain") or []
        # A reused card's trace belongs to the pulse that EARNED it. A later
        # run that recognised the same open proposal spent no agent turn on
        # it, and a receipt that charged it anyway would make an idempotent
        # re-poll look as expensive as the first sweep.
        action_ids = [
            link["action_id"]
            for link in chain
            if isinstance(link, dict)
            and isinstance(link.get("action_id"), int)
            and not link.get("reused")
        ]
        turns, measured_ms, unmeasured, seats = 0, 0.0, 0, 0
        if action_ids:
            placeholders = ",".join("?" for _ in action_ids)
            for action in conn.execute(
                f"SELECT detail_json FROM actions WHERE id IN ({placeholders})",  # noqa: S608
                action_ids,
            ):
                for hop in _trace_of(action["detail_json"]):
                    if not isinstance(hop, dict):
                        continue
                    if hop.get("step") not in AGENT_HOP_STEPS:
                        continue
                    turns += 1
                    # A panel turn is several reviewers in one hop; the
                    # seat count is what the quota actually paid for.
                    if hop.get("step") == "panel":
                        seats += int(hop.get("answered") or 0)
                    duration = hop.get("duration_ms")
                    if isinstance(duration, (int, float)):
                        measured_ms += float(duration)
                    else:
                        unmeasured += 1
        reflex_ms = report.get("reflex_ms")
        reflex_ms = float(reflex_ms) if isinstance(reflex_ms, (int, float)) else None
        receipts.append(
            {
                "pulse_id": row["id"],
                "ran_at": row["created_at"],
                "mission": report.get("mission"),
                "signals": report.get("signals", 0),
                "analyzed": report.get("analyzed", 0),
                "proposals_filed": report.get("proposals_filed", 0),
                "proposals_reused": report.get("proposals_reused", 0),
                "agent_turns": turns,
                "panel_seats_answered": seats,
                "unmeasured_turns": unmeasured,
                "reflex_ms": reflex_ms,
                "agent_ms": round(measured_ms, 1),
                "wall_clock_ms": round(measured_ms + (reflex_ms or 0.0), 1),
                "llm_budget": report.get("llm_budget", 0),
                "llm_calls_used": report.get("llm_calls_used", 0),
                "budget_exhausted": bool(report.get("budget_exhausted", False)),
            }
        )
    return receipts
