"""The operations surface — running the system, not the product it sells.

``POST /ops/demo-reset`` is rehearsal hygiene: it clears the decision
state (events, actions, decisions, idempotency claims, pulse log) so a
rehearsal or jury run starts from a clean stage. The AI ledgers —
``ai_usage`` and ``llm_cache`` — are deliberately preserved: quota
history is real spend and must never be rewritten.

``?seed=1`` follows the wipe with a handful of synthetic PAST operator
verdicts (clearly attributed to the demo seed), so the panels that feed
on decision memory — the memory fold, the funnel, the ROI table — do not
greet the jury empty right after a reset. The reset is inert unless
``SENTINEL_DEMO_RESET=1``: without the knob it answers 404,
indistinguishable from not existing.

``GET /ops/health/watch`` publishes the standing watch's own vitals — the
question ``/health`` structurally cannot answer, because a sentinel that
froze three hours ago still has a perfectly live process behind it.

``GET /ops/preflight`` is ``docs/DEMO_PREFLIGHT.md`` as code: the checks a
human currently runs by eye before a take, in one call that answers with a
single ``ok``.
"""

import logging
import os
import sqlite3
import tempfile
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import db, feeds, ledger, watchdog
from app.models import DemoResetReport
from app.watchdog import WatchHealth

logger = logging.getLogger("cloudsentinel.ops")

router = APIRouter(prefix="/ops", tags=["ops"])

DEMO_RESET_ENV = "SENTINEL_DEMO_RESET"
READONLY_ENV = "SENTINEL_READONLY"

# FK-safe wipe order: action_events and decisions reference actions,
# actions reference events. The audit chain goes first and goes WITH them:
# it seals those exact rows, so a chain left standing over a wiped table
# would report a stage reset as tampering — which is the one thing a
# tamper-evident ledger must not do.
CLEARED_TABLES = (
    "audit_ledger",
    "action_events",
    "decisions",
    "actions",
    "events",
    "idempotency",
    "pulse_log",
)

SEED_ACTOR_NOTE = "seeded demo verdict"

# (service, verdict, rationale, hours_ago) — mixed outcomes with concrete
# rationales, so the memory digest and the calibration read like history.
SEED_DECISIONS = (
    ("compute", "approved", "idle capacity confirmed by the owning team — right-size approved", 160),
    ("compute", "rejected", "known migration window; spike expected to clear itself", 120),
    ("database", "approved", "read replica tier lowered after the maintenance check", 100),
    ("database", "rejected", "billing re-statement artifact — no workload change", 76),
    ("storage", "approved", "lifecycle rule applied to the archive bucket", 52),
    ("network", "rejected", "egress spike traced to a planned data export", 28),
)


def demo_reset_enabled() -> bool:
    return os.environ.get(DEMO_RESET_ENV, "").strip() == "1"


@router.get("/health/watch")
def watch_health(conn: sqlite3.Connection = Depends(db.get_db)) -> WatchHealth:
    """Is the sentinel still watching? (``/health`` only knows the process is up.)

    On 1 August the deployed watch stopped beating at 20:40 and nothing
    said so for three hours — ``/health`` answered 200 the whole time,
    correctly, because the process was fine. This endpoint answers the
    other question: last successful beat and its age, consecutive failed
    ticks, the configured cadence, and whether the gap has crossed the
    staleness line. Read-only, unauthenticated and cheap, so an uptime
    monitor can watch the watchman.
    """
    return watchdog.health(conn)


# --- preflight: the runbook, executable ---------------------------------------


CheckStatus = Literal["pass", "warn", "fail"]


class PreflightCheck(BaseModel):
    """One thing a human would otherwise verify by eye before a take."""

    name: str
    status: CheckStatus
    detail: str


class PreflightReport(BaseModel):
    """``docs/DEMO_PREFLIGHT.md`` with a verdict instead of a checklist.

    ``ok`` is false only on a ``fail`` — something that would visibly break
    a demo. A ``warn`` is a posture worth knowing about (live provider,
    writes open, a lane quietly serving the fixture) that the operator may
    have chosen on purpose, so it never fails the flag on its own.
    """

    ok: bool
    failures: int
    warnings: int
    checks: list[PreflightCheck]
    note: str


def _readonly_enabled() -> bool:
    # Read straight from the env rather than importing main: the entry point
    # imports this router, so the arrow only points one way.
    return os.environ.get(READONLY_ENV, "").strip() == "1"


def _check_dataset() -> PreflightCheck:
    from app.detection import load_dataset

    try:
        dataset = load_dataset()
        rows = len(dataset["daily_costs"])
        period = dataset["period"]
        services = sorted({row["service"] for row in dataset["daily_costs"]})
    except Exception as error:
        return PreflightCheck(
            name="dataset", status="fail", detail=f"dataset failed to load: {error}"
        )
    if not rows:
        return PreflightCheck(
            name="dataset", status="fail", detail="dataset loaded but holds no cost rows"
        )
    return PreflightCheck(
        name="dataset",
        status="pass",
        detail=(
            f"{rows} cost rows over {period['start']}→{period['end']}, "
            f"{len(services)} services ({', '.join(services)})"
        ),
    )


def _check_mission() -> PreflightCheck:
    from app.missions import get_mission

    try:
        mission = get_mission()
    except Exception as error:
        return PreflightCheck(
            name="mission", status="fail", detail=f"mission config unavailable: {error}"
        )
    return PreflightCheck(
        name="mission", status="pass", detail=f"resolved to '{mission.mission}'"
    )


def _check_provider() -> PreflightCheck:
    from app.llm import provider_mode

    mode = provider_mode()
    if mode == "fake":
        return PreflightCheck(
            name="provider",
            status="pass",
            detail="deterministic fake — no key, no quota, no network gamble",
        )
    return PreflightCheck(
        name="provider",
        status="warn",
        detail=(
            f"live provider '{mode}' — a take will spend real quota; the fake "
            "path is the one the reliability story rests on"
        ),
    )


def _check_readonly() -> PreflightCheck:
    if _readonly_enabled():
        return PreflightCheck(
            name="readonly",
            status="pass",
            detail=(
                "read-only showcase — writes answer 403; plan no approve/reject "
                "beat against this instance"
            ),
        )
    return PreflightCheck(
        name="readonly",
        status="warn",
        detail=(
            "writes are open — right for the local stage, wrong for a public link"
        ),
    )


def _check_watch(conn: sqlite3.Connection) -> PreflightCheck:
    watch = watchdog.health(conn)
    if watch.degraded:
        return PreflightCheck(name="watchdog", status="fail", detail=watch.detail)
    return PreflightCheck(
        name="watchdog",
        status="pass" if watch.configured else "warn",
        detail=watch.detail,
    )


def _check_pulse_age(conn: sqlite3.Connection) -> PreflightCheck:
    """A stage whose last scan is hours old shows the jury stale panels."""
    watch = watchdog.health(conn)
    if watch.last_pulse_at is None:
        return PreflightCheck(
            name="last_pulse",
            status="warn",
            detail=(
                "no pulse on record — the decision desk is empty; run POST /pulse "
                "(or let the watch beat) before the camera rolls"
            ),
        )
    age = watch.last_pulse_age_seconds or 0.0
    detail = f"last scan {age / 60:.0f} min ago ({watch.last_pulse_at})"
    # An hour is generous for a rehearsal and still catches the frozen stage.
    return PreflightCheck(
        name="last_pulse",
        status="pass" if age <= 3600 else "warn",
        detail=detail if age <= 3600 else detail + " — panels will look stale",
    )


def _check_disk() -> PreflightCheck:
    """The database's directory has to accept a write, or nothing persists."""
    directory = db.db_path().parent
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".preflight-"):
            pass
    except OSError as error:
        return PreflightCheck(
            name="disk",
            status="fail",
            detail=f"{directory} is not writable: {error.strerror or error}",
        )
    return PreflightCheck(
        name="disk", status="pass", detail=f"{directory} accepts writes"
    )


def _check_feeds() -> PreflightCheck:
    """What the lanes actually serve, not what they were configured to."""
    try:
        sources = feeds.data_sources()
    except Exception as error:
        return PreflightCheck(
            name="data_sources", status="fail", detail=f"unreadable: {error}"
        )
    rendered = ", ".join(f"{lane}={source}" for lane, source in sorted(sources.items()))
    # A lane that fell back reports "mock (feed unavailable)" — the badge
    # cannot claim live data over mock panels, and neither can we.
    fell_back = [lane for lane, source in sources.items() if "unavailable" in source]
    if fell_back:
        return PreflightCheck(
            name="data_sources",
            status="warn",
            detail=f"{rendered} — {', '.join(fell_back)} fell back to the fixture",
        )
    return PreflightCheck(name="data_sources", status="pass", detail=rendered)


def _check_security_headers() -> PreflightCheck:
    """The policy this instance will stamp on every response.

    Read from the entry point's own constants (imported late — main imports
    this module), so it reports what is configured. That it survives to the
    wire is what ``scripts/smoke.sh`` and the header assertions check.
    """
    try:
        from main import CONTENT_SECURITY_POLICY, SECURITY_HEADERS
    except Exception as error:
        return PreflightCheck(
            name="security_headers", status="fail", detail=f"unreadable: {error}"
        )
    missing = [
        header
        for header in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy")
        if header not in SECURITY_HEADERS
    ]
    if missing or "script-src 'self'" not in CONTENT_SECURITY_POLICY:
        return PreflightCheck(
            name="security_headers",
            status="fail",
            detail=(
                f"missing {missing}" if missing else "CSP no longer locks script-src"
            ),
        )
    return PreflightCheck(
        name="security_headers",
        status="pass",
        detail=(
            f"CSP script-src 'self' + {len(SECURITY_HEADERS)} headers on every response"
        ),
    )


def _check_demo_reset() -> PreflightCheck:
    """Between-takes recovery: is the reset lever actually there?"""
    if demo_reset_enabled():
        return PreflightCheck(
            name="demo_reset",
            status="pass",
            detail="POST /ops/demo-reset?seed=1 is armed for between-take resets",
        )
    return PreflightCheck(
        name="demo_reset",
        status="warn",
        detail=(
            "demo reset is off (404) — correct for a public link, but there is "
            "no way to re-stage between takes on this instance"
        ),
    )


@router.get("/preflight")
def preflight(conn: sqlite3.Connection = Depends(db.get_db)) -> PreflightReport:
    """Run the whole demo checklist in one call and answer with one flag.

    ``docs/DEMO_PREFLIGHT.md`` is a human checklist, which means it is only
    as reliable as the human running it at midnight before a deadline. This
    is the same list executed: dataset, mission, provider, write posture,
    the standing watch, how old the last scan is, whether the disk takes a
    write, what the lanes are really serving, the security headers, and
    whether the between-takes reset lever exists.

    One request before a take; one link the jury can click.
    """
    checks = [
        _check_dataset(),
        _check_mission(),
        _check_provider(),
        _check_readonly(),
        _check_watch(conn),
        _check_pulse_age(conn),
        _check_disk(),
        _check_feeds(),
        _check_security_headers(),
        _check_demo_reset(),
    ]
    failures = sum(1 for check in checks if check.status == "fail")
    warnings = sum(1 for check in checks if check.status == "warn")
    logger.info("[OPS] preflight — %d fail, %d warn", failures, warnings)
    return PreflightReport(
        ok=failures == 0,
        failures=failures,
        warnings=warnings,
        checks=checks,
        note=(
            "A warn is a posture, not a defect — a live provider or open "
            "writes may be exactly what this instance is for. Only a fail "
            "clears ok."
        ),
    )


def _seed_decisions(conn: sqlite3.Connection) -> int:
    """Insert the synthetic verdict history (inside the caller's txn)."""
    for service, verdict, rationale, hours_ago in SEED_DECISIONS:
        conn.execute(
            "INSERT INTO decisions "
            "(action_id, service, verdict, rationale, input_context_json, created_at) "
            "VALUES (NULL, ?, ?, ?, ?, datetime('now', ?))",
            (
                service,
                verdict,
                rationale,
                f'{{"origin": "{SEED_ACTOR_NOTE}"}}',
                f"-{hours_ago} hours",
            ),
        )
    return len(SEED_DECISIONS)


@router.post(
    "/demo-reset",
    responses={404: {"description": "Demo reset is not enabled on this deployment."}},
)
def demo_reset(
    seed: bool = Query(
        False,
        description=(
            "Also seed a few synthetic past operator verdicts so the "
            "memory-fed panels stay populated after the wipe."
        ),
    ),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> DemoResetReport:
    """Clear the decision state for a clean rehearsal stage (env-gated)."""
    if not demo_reset_enabled():
        raise HTTPException(
            status_code=404, detail="demo reset is not enabled on this deployment"
        )
    with db.writing(conn):
        # The chain builds its own table lazily on first seal, so a
        # deployment that has never recorded a verdict would answer the
        # wipe with "no such table: audit_ledger".
        ledger.ensure_schema(conn)
        for table in CLEARED_TABLES:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 — fixed table list
        seeded = _seed_decisions(conn) if seed else 0
    logger.info("[OPS] demo reset — cleared %s, seeded %d", CLEARED_TABLES, seeded)
    return DemoResetReport(
        cleared=list(CLEARED_TABLES),
        seeded_decisions=seeded,
        note=(
            "ai_usage and llm_cache preserved — quota history is real spend "
            "and is never rewritten"
        ),
    )
