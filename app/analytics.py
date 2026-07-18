"""Aggregate analytics over signals, decisions and agent usage (Sprint 3).

Business-intelligence layer, deliberately LLM-free: every number here is
plain SQL plus Python arithmetic over data the pipeline has already
persisted, so the endpoints cost zero quota and every figure is
reproducible. Money totals are summed from the Recommender's
deterministic ``savings`` block — never generated.

Answers the operator's standing questions:

- funnel: how many signals became analyses, proposals and decisions?
- quality: how favorably and how fast do humans decide, and what is the
  estimated monthly value of what they approved?
- telemetry: what did the agents do (triage mix, confidence, debates,
  cache discipline)?
- trend: where is spend going, window over window, and which services
  moved the most?
"""

import calendar
import json
import logging
import os
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import bus, db
from app.actions import TIMEOUT_ACTOR, expire_stale_proposals
from app.detection import build_daily_series, load_dataset
from app.models import (
    AgentTelemetry,
    CostTrendReport,
    DecisionAnalyticsReport,
    DecisionQuality,
    DetectionPrecisionReport,
    DetectionPrecisionRow,
    HitlFunnel,
    ServiceTrendRow,
)

logger = logging.getLogger("cloudsentinel.analytics")

router = APIRouter(prefix="/analytics", tags=["analytics"])
metrics_router = APIRouter(prefix="/metrics", tags=["analytics"])

PRECISION_METHOD = (
    "operator approvals over decided proposals; a rejection is treated "
    "as a detector false positive (proxy ground truth)"
)

SAVINGS_METHOD = (
    "sum of the preferred option's deterministic monthly projection "
    "over approved and executed actions"
)


def _funnel(conn: sqlite3.Connection) -> HitlFunnel:
    # The funnel narrates the COST pipeline (detect -> analyze -> recommend
    # -> decide); security events persist in the same table but are barred
    # from the agents, so counting them here would fake the conversion story.
    signals = conn.execute(
        "SELECT count(*) FROM events WHERE kind = 'cost_anomaly'"
    ).fetchone()[0]
    analyzed = conn.execute(
        "SELECT count(*) FROM events WHERE kind = 'cost_anomaly' "
        "AND analysis_json IS NOT NULL"
    ).fetchone()[0]
    # Actions now span lanes (fraud holds, the budget guard); the funnel
    # stays the COST conversion story, so scope by the linked event's kind.
    # Legacy rows without an event id predate the other lanes — cost.
    cost_scope = (
        "LEFT JOIN events e ON e.id = a.event_id "
        "WHERE (e.kind = 'cost_anomaly' OR a.event_id IS NULL)"
    )
    states = {
        row["state"]: row["n"]
        for row in conn.execute(
            f"SELECT a.state AS state, count(*) AS n FROM actions a {cost_scope} "
            "GROUP BY a.state"
        )
    }
    timeout_rejections = conn.execute(
        f"SELECT count(*) FROM actions a {cost_scope} AND a.decided_by = ?",
        (TIMEOUT_ACTOR,),
    ).fetchone()[0]
    return HitlFunnel(
        signals=signals,
        analyzed=analyzed,
        proposals=sum(states.values()),
        pending=states.get("proposed", 0),
        approved=states.get("approved", 0),
        rejected=states.get("rejected", 0),
        executed=states.get("executed", 0),
        timeout_rejections=timeout_rejections,
    )


def _quality(conn: sqlite3.Connection) -> DecisionQuality:
    # The decisions table records human verdicts only (timeout expiries
    # bypass it by design), so rates computed here measure the operators.
    verdicts = {
        row["verdict"]: row["n"]
        for row in conn.execute(
            "SELECT verdict, count(*) AS n FROM decisions GROUP BY verdict"
        )
    }
    human_decisions = sum(verdicts.values())
    approval_rate = (
        round(verdicts.get("approved", 0) / human_decisions, 4)
        if human_decisions
        else None
    )
    hours = conn.execute(
        "SELECT avg((julianday(decided_at) - julianday(proposed_at)) * 24.0) "
        "FROM actions WHERE decided_at IS NOT NULL AND decided_by != ?",
        (TIMEOUT_ACTOR,),
    ).fetchone()[0]

    savings_total = 0.0
    for row in conn.execute(
        "SELECT detail_json FROM actions WHERE state IN ('approved', 'executed')"
    ):
        # Corrupt detail must degrade to "skipped row", never to a 500 —
        # same tolerance the decide endpoint applies to the same column.
        try:
            detail = json.loads(row["detail_json"])
            block = detail["savings"]
            key = (
                "bold_monthly"
                if detail.get("preferred") == "BOLD"
                else "cautious_monthly"
            )
            savings_total += float(block[key])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue

    return DecisionQuality(
        human_decisions=human_decisions,
        approval_rate=approval_rate,
        avg_decision_hours=round(hours, 4) if hours is not None else None,
        approved_estimated_monthly_savings=round(savings_total, 2),
        savings_method=SAVINGS_METHOD,
    )


def _telemetry(conn: sqlite3.Connection) -> AgentTelemetry:
    triage: dict[str, int] = {}
    scores: list[float] = []
    for row in conn.execute(
        "SELECT analysis_json FROM events WHERE kind = 'cost_anomaly' "
        "AND analysis_json IS NOT NULL"
    ):
        # Parse everything before mutating state: a row with a valid triage
        # but corrupt confidence must be skipped whole, not half-counted.
        try:
            report = json.loads(row["analysis_json"])["report"]
            kind = report["triage"]
            score = float(report["confidence"]["score"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        triage[kind] = triage.get(kind, 0) + 1
        scores.append(score)
    by_source = {
        row["source"]: row["n"]
        for row in conn.execute(
            "SELECT source, count(*) AS n FROM ai_usage GROUP BY source"
        )
    }
    by_agent = {
        row["agent"]: row["n"]
        for row in conn.execute(
            "SELECT agent, count(*) AS n FROM ai_usage GROUP BY agent"
        )
    }
    cache_hits = conn.execute(
        "SELECT count(*) FROM ai_usage WHERE from_cache = 1"
    ).fetchone()[0]
    # Did the skeptic ever CHANGE an outcome? Count persisted transcripts
    # whose verdict overturned the draft stance — the debate's measurable
    # effect, not just its occurrence.
    overturned = 0
    for row in conn.execute("SELECT detail_json FROM actions"):
        try:
            transcript = json.loads(row["detail_json"]).get("transcript")
        except json.JSONDecodeError:
            continue
        if isinstance(transcript, dict) and transcript.get("agreed") is False:
            overturned += 1

    return AgentTelemetry(
        triage_distribution=dict(sorted(triage.items())),
        avg_confidence=round(sum(scores) / len(scores), 4) if scores else None,
        requests_total=sum(by_agent.values()),
        cache_hits=cache_hits,
        by_source=by_source,
        by_agent=by_agent,
        debates=by_agent.get("skeptic", 0),
        debates_overturned=overturned,
    )


@router.get("/decisions")
def decision_analytics(
    conn: sqlite3.Connection = Depends(db.get_db),
) -> DecisionAnalyticsReport:
    """Return the HITL funnel, decision quality and agent telemetry."""
    # Same sweep GET /actions runs: under request-triggered deployment the
    # pending count is only truthful after stale proposals have expired.
    expire_stale_proposals(conn)
    return DecisionAnalyticsReport(
        funnel=_funnel(conn),
        quality=_quality(conn),
        telemetry=_telemetry(conn),
    )


@metrics_router.get("/detection")
def detection_precision(
    window_days: int = Query(
        30,
        ge=1,
        le=365,
        description="How many days of operator verdicts to measure over.",
    ),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> DetectionPrecisionReport:
    """Operator verdicts as detector ground truth (the P1.5 feedback loop).

    Decision memory already records every human verdict; this endpoint
    closes the loop by reading rejections as detector false positives.
    Coarse by design — a rejection can also mean "real but not worth
    acting on" — hence the explicit proxy label in ``method``.
    """
    rows = conn.execute(
        "SELECT service, verdict, count(*) AS n FROM decisions "
        "WHERE created_at >= datetime('now', ?) GROUP BY service, verdict",
        (f"-{window_days} days",),
    ).fetchall()
    per_service: dict[str, dict[str, int]] = {}
    for row in rows:
        counts = per_service.setdefault(row["service"], {"approved": 0, "rejected": 0})
        counts[row["verdict"]] = row["n"]

    def proxy(approved: int, rejected: int) -> float | None:
        decided = approved + rejected
        return round(approved / decided, 4) if decided else None

    services = [
        DetectionPrecisionRow(
            service=service,
            approved=counts["approved"],
            rejected=counts["rejected"],
            precision_proxy=proxy(counts["approved"], counts["rejected"]),
        )
        for service, counts in sorted(per_service.items())
    ]
    approved = sum(row.approved for row in services)
    rejected = sum(row.rejected for row in services)
    return DetectionPrecisionReport(
        window_days=window_days,
        approved=approved,
        rejected=rejected,
        decided=approved + rejected,
        precision_proxy=proxy(approved, rejected),
        method=PRECISION_METHOD,
        services=services,
    )


# --- self-FinOps: the watcher watches its own AI spend (S3 stretch) ---------
#
# Free-tier working assumption from the locked plan; the live spike verifies
# the real numbers. Zero-cost by construction (billing-disabled project), so
# the meaningful counter is calls against the daily quota, not money.

RPD_ASSUMPTION = 250
RPM_ASSUMPTION = 10

AI_USAGE_NOTE = (
    "assumed free-tier limits (10 RPM / 250 RPD) pending the live spike; "
    "zero-cost by construction — billing-disabled project, cached and "
    "fallback answers consume no quota"
)


class AiUsageDay(BaseModel):
    date: str
    calls: int


class AiUsageReport(BaseModel):
    requests_total: int
    live_calls: int
    cache_hits: int
    fallback_answers: int
    live_calls_today: int
    rpd_assumption: int
    rpd_used_pct: float
    by_agent: dict[str, int]
    last_seven_days: list[AiUsageDay]
    note: str


@router.get("/ai")
def ai_usage(conn: sqlite3.Connection = Depends(db.get_db)) -> AiUsageReport:
    """Self-FinOps: the cost watcher accounts for its own AI usage."""
    by_source = {
        row["source"]: row["n"]
        for row in conn.execute(
            "SELECT source, count(*) AS n FROM ai_usage GROUP BY source"
        )
    }
    by_agent = {
        row["agent"]: row["n"]
        for row in conn.execute(
            "SELECT agent, count(*) AS n FROM ai_usage GROUP BY agent"
        )
    }
    cache_hits = conn.execute(
        "SELECT count(*) FROM ai_usage WHERE from_cache = 1"
    ).fetchone()[0]
    live_today = conn.execute(
        "SELECT count(*) FROM ai_usage WHERE source = 'gemini' "
        "AND from_cache = 0 AND date(created_at) = date('now')"
    ).fetchone()[0]
    days = [
        AiUsageDay(date=row["d"], calls=row["n"])
        for row in conn.execute(
            "SELECT date(created_at) AS d, count(*) AS n FROM ai_usage "
            "WHERE created_at >= datetime('now', '-7 days') "
            "GROUP BY d ORDER BY d"
        )
    ]
    return AiUsageReport(
        requests_total=sum(by_agent.values()),
        live_calls=by_source.get("gemini", 0),
        cache_hits=cache_hits,
        fallback_answers=by_source.get("fallback", 0),
        live_calls_today=live_today,
        rpd_assumption=RPD_ASSUMPTION,
        rpd_used_pct=round(live_today / RPD_ASSUMPTION * 100, 1),
        by_agent=by_agent,
        last_seven_days=days,
        note=AI_USAGE_NOTE,
    )


# --- forecast: month-end projection from the daily series (S3 value pack) ----

MONTHLY_BUDGET_ENV = "SENTINEL_MONTHLY_BUDGET"

FORECAST_METHOD = (
    "ordinary least squares over the daily totals, extrapolated to the end "
    "of the last observed month — deterministic arithmetic, no generation"
)


def monthly_budget() -> float | None:
    raw = os.environ.get(MONTHLY_BUDGET_ENV, "").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


class CostForecastReport(BaseModel):
    method: str
    history_days: int
    slope_per_day: float
    month: str
    days_in_month: int
    observed_days_in_month: int
    month_to_date: float
    projected_month_total: float
    monthly_budget: float | None
    projected_over_budget: bool | None
    note: str


@router.get("/costs/forecast")
def cost_forecast() -> CostForecastReport:
    """Project the current month's total spend from the observed daily trend."""
    dataset = load_dataset()
    series = build_daily_series(dataset["daily_costs"])
    dates, totals = series["dates"], series["totals"]
    if len(dates) < 2:
        raise HTTPException(
            status_code=409, detail="forecast needs at least two observed days"
        )
    count = len(totals)
    mean_x = (count - 1) / 2
    mean_y = sum(totals) / count
    var_x = sum((i - mean_x) ** 2 for i in range(count))
    slope = (
        sum((i - mean_x) * (totals[i] - mean_y) for i in range(count)) / var_x
    )
    intercept = mean_y - slope * mean_x

    last_day = date.fromisoformat(dates[-1])
    days_in_month = calendar.monthrange(last_day.year, last_day.month)[1]
    month_prefix = dates[-1][:7]
    month_to_date = round(
        sum(total for d, total in zip(dates, totals) if d.startswith(month_prefix)), 2
    )
    observed_in_month = sum(1 for d in dates if d.startswith(month_prefix))
    remaining = days_in_month - last_day.day
    projected_rest = sum(
        max(0.0, intercept + slope * (count - 1 + step))
        for step in range(1, remaining + 1)
    )
    projected = round(month_to_date + projected_rest, 2)
    budget = monthly_budget()
    return CostForecastReport(
        method=FORECAST_METHOD,
        history_days=count,
        slope_per_day=round(slope, 2),
        month=month_prefix,
        days_in_month=days_in_month,
        observed_days_in_month=observed_in_month,
        month_to_date=month_to_date,
        projected_month_total=projected,
        monthly_budget=budget,
        projected_over_budget=(projected > budget) if budget is not None else None,
        note=(
            "negative daily predictions clamp to zero; the projection covers "
            f"the {remaining} unobserved day(s) of {month_prefix}"
        ),
    )


CALIBRATION_METHOD = (
    "operator approval rate per recommendation-confidence bucket; a "
    "well-calibrated agent earns more approvals as its own confidence rises"
)

CALIBRATION_BUCKETS = ((0.0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01))


class CalibrationBucket(BaseModel):
    range: str
    decisions: int
    approved: int
    approval_rate: float | None


class CalibrationReport(BaseModel):
    method: str
    decisions_with_confidence: int
    buckets: list[CalibrationBucket]
    note: str


@router.get("/calibration")
def confidence_calibration(
    conn: sqlite3.Connection = Depends(db.get_db),
) -> CalibrationReport:
    """Does the agent KNOW how good it is? Confidence vs human verdicts."""
    counts = [[0, 0] for _ in CALIBRATION_BUCKETS]  # [decisions, approved]
    considered = 0
    for row in conn.execute(
        "SELECT verdict, input_context_json FROM decisions"
    ):
        try:
            confidence = json.loads(row["input_context_json"])["confidence"]["score"]
            confidence = float(confidence)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue  # seeded verdicts and non-cost lanes carry no confidence
        for index, (low, high) in enumerate(CALIBRATION_BUCKETS):
            if low <= confidence < high:
                counts[index][0] += 1
                if row["verdict"] == "approved":
                    counts[index][1] += 1
                considered += 1
                break
    buckets = [
        CalibrationBucket(
            range=f"{low:.1f}–{min(high, 1.0):.1f}",
            decisions=decided,
            approved=approved,
            approval_rate=round(approved / decided, 4) if decided else None,
        )
        for (low, high), (decided, approved) in zip(CALIBRATION_BUCKETS, counts)
    ]
    return CalibrationReport(
        method=CALIBRATION_METHOD,
        decisions_with_confidence=considered,
        buckets=buckets,
        note=(
            "decisions without a recorded confidence (seeded history, "
            "fraud holds, the budget guard) are excluded"
        ),
    )


class HeadlineReport(BaseModel):
    headline: str
    generated_from: list[str]


@router.get("/headline")
def analytics_headline(
    conn: sqlite3.Connection = Depends(db.get_db),
) -> HeadlineReport:
    """One jury-ready sentence, composed from the persisted aggregates."""
    funnel = _funnel(conn)
    quality = _quality(conn)
    parts = [
        f"{funnel.signals} signals → {funnel.analyzed} analyzed → "
        f"{funnel.proposals} proposals",
        f"{quality.human_decisions} human decision"
        f"{'' if quality.human_decisions == 1 else 's'}"
        + (
            f" ({round(quality.approval_rate * 100)}% approved)"
            if quality.approval_rate is not None
            else ""
        ),
        f"{quality.approved_estimated_monthly_savings:.2f}/mo estimated "
        "approved savings — computed, never generated",
    ]
    return HeadlineReport(
        headline="; ".join(parts) + ".",
        generated_from=["/analytics/decisions"],
    )


BUDGET_ACTION_NOTE = (
    "deterministic budget guard — the forecast arithmetic projected a "
    "month-end overrun; deciding this card records the operator's stance, "
    "nothing is throttled automatically"
)


def file_budget_risk_action(conn: sqlite3.Connection) -> int:
    """File one HITL card when the forecast projects a month-end overrun.

    Pure arithmetic, no LLM: the trigger is ``projected_over_budget`` from
    the same OLS forecast the dashboard shows, and the two options are
    fixed templates. One card per calendar month (natural key on the
    budget_risk event); a rejected card may be re-filed by a later sweep.
    Inert unless SENTINEL_MONTHLY_BUDGET is set.
    """
    if monthly_budget() is None:
        return 0
    try:
        forecast = cost_forecast()
    except HTTPException:
        return 0  # not enough observed days — nothing to guard yet
    if not forecast.projected_over_budget:
        return 0
    overage = round(forecast.projected_month_total - forecast.monthly_budget, 2)
    detail = {
        "kind": "budget_risk",
        "category": "BUDGET_GUARD",
        "forecast": forecast.model_dump(),
        "overage": overage,
        "options": [
            {
                "stance": "CAUTIOUS",
                "title": "Freeze non-essential scale-ups pending review",
                "description": (
                    "Hold elective capacity increases until the owning teams "
                    "confirm the spend trajectory; revisit after the next "
                    "billing day."
                ),
                "risk": "low",
                "rollback": "available — lift the freeze at any time",
            },
            {
                "stance": "BOLD",
                "title": "Convene a same-day spend review on the top mover",
                "description": (
                    "Pull the owning team of the largest contributor into a "
                    "same-day review and stage an immediate right-sizing "
                    "decision."
                ),
                "risk": "medium",
                "rollback": "not applicable — review only",
            },
        ],
        "preferred": "CAUTIOUS",
        "note": BUDGET_ACTION_NOTE,
    }
    with db.writing(conn):
        event_id = db.upsert_event(
            conn,
            kind="budget_risk",
            service="monthly-budget",
            occurred_on=f"{forecast.month}-01",
            payload_json=json.dumps(
                {
                    "month": forecast.month,
                    "projected": forecast.projected_month_total,
                    "budget": forecast.monthly_budget,
                    "overage": overage,
                }
            ),
        )
        open_card = conn.execute(
            "SELECT 1 FROM actions WHERE event_id = ? AND state != 'rejected' LIMIT 1",
            (event_id,),
        ).fetchone()
        if open_card is not None:
            return 0
        conn.execute(
            "INSERT INTO actions (event_id, title, detail_json) VALUES (?, ?, ?)",
            (
                event_id,
                f"Budget guard — {forecast.month} projected "
                f"{forecast.projected_month_total:.2f} vs {forecast.monthly_budget:.2f}",
                json.dumps(detail),
            ),
        )
    logger.info(
        "[BUDGET] %s",
        json.dumps(
            {
                "month": forecast.month,
                "projected": forecast.projected_month_total,
                "budget": forecast.monthly_budget,
            },
            sort_keys=True,
        ),
    )
    bus.emit(
        conn,
        "budget-guard",
        "card",
        f"{forecast.month} projected {forecast.projected_month_total:.2f} vs "
        f"budget {forecast.monthly_budget:.2f} — guard card filed for the operator",
    )
    return 1


# --- what-if: the Time Machine embryo, deterministic (S3 value pack) --------


class WhatIfReport(BaseModel):
    action_id: int
    action_state: str
    service: str
    stance: str
    current_monthly_projection: float
    monthly_saving_if_executed: float
    with_action_monthly_projection: float
    method: str
    note: str


@router.get("/whatif")
def what_if(
    action_id: int = Query(ge=1, description="Action id from GET /actions."),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> WhatIfReport:
    """Project a service's monthly spend with and without a recommendation.

    Pure arithmetic over the recommendation's computed savings block —
    the simulation-only counterpart of "what happens if the operator
    approves this".
    """
    row = conn.execute(
        "SELECT * FROM actions WHERE id = ?", (action_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"action {action_id} does not exist")
    try:
        detail = json.loads(row["detail_json"])
        savings = detail["savings"]
        anomaly = detail.get("anomaly", {})
        service = anomaly.get("service") or "unknown"
        stance = detail.get("preferred", "CAUTIOUS")
        saving = float(
            savings["bold_monthly" if stance == "BOLD" else "cautious_monthly"]
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=409,
            detail=f"action {action_id} has no computed savings recorded",
        ) from error

    dataset = load_dataset()
    series = build_daily_series(dataset["daily_costs"])
    values = next(
        (svc.values for svc in series["services"] if svc.service == service), None
    )
    daily_mean = (sum(values) / len(values)) if values else 0.0
    current_monthly = round(daily_mean * 30, 2)
    return WhatIfReport(
        action_id=action_id,
        action_state=row["state"],
        service=service,
        stance=stance,
        current_monthly_projection=current_monthly,
        monthly_saving_if_executed=round(saving, 2),
        with_action_monthly_projection=round(max(0.0, current_monthly - saving), 2),
        method=(
            "service daily mean x 30 days, minus the recommendation's "
            "deterministic monthly saving"
        ),
        note="projection only — execution stays simulated and operator-gated",
    )


# --- ROI: before/after observation around each approved action --------------


ROI_METHOD = (
    "daily-mean comparison of the service's observed spend before and after "
    "the decision timestamp; estimates come from the recommendation's "
    "deterministic savings block"
)


class RoiRow(BaseModel):
    action_id: int
    service: str
    state: str
    decided_at: str
    estimated_monthly_saving: float
    before_daily_mean: float | None
    after_daily_mean: float | None
    after_days: int
    observed_monthly_delta: float | None
    status: str  # observed | estimated_only


class RoiReport(BaseModel):
    method: str
    note: str
    rows: list[RoiRow]


@router.get("/roi")
def roi(conn: sqlite3.Connection = Depends(db.get_db)) -> RoiReport:
    """Track whether approved actions were followed by lower observed spend.

    Honest by construction: with no post-decision days in the dataset the
    row says ``estimated_only`` instead of inventing an observation.
    """
    dataset = load_dataset()
    series = build_daily_series(dataset["daily_costs"])
    by_service = {svc.service: svc.values for svc in series["services"]}
    dates = series["dates"]

    rows: list[RoiRow] = []
    for action in conn.execute(
        "SELECT * FROM actions WHERE state IN ('approved', 'executed') ORDER BY id"
    ):
        try:
            detail = json.loads(action["detail_json"])
            savings = detail["savings"]
            stance = detail.get("preferred", "CAUTIOUS")
            saving = float(
                savings["bold_monthly" if stance == "BOLD" else "cautious_monthly"]
            )
            service = detail.get("anomaly", {}).get("service") or "unknown"
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue  # corrupt detail: same skip tolerance as the funnel
        decided_day = str(action["decided_at"] or "")[:10]
        values = by_service.get(service, [])
        before = [v for d, v in zip(dates, values) if d <= decided_day]
        after = [v for d, v in zip(dates, values) if d > decided_day]
        before_mean = round(sum(before) / len(before), 2) if before else None
        after_mean = round(sum(after) / len(after), 2) if after else None
        observed = (
            round((before_mean - after_mean) * 30, 2)
            if before_mean is not None and after_mean is not None
            else None
        )
        rows.append(
            RoiRow(
                action_id=action["id"],
                service=service,
                state=action["state"],
                decided_at=str(action["decided_at"]),
                estimated_monthly_saving=round(saving, 2),
                before_daily_mean=before_mean,
                after_daily_mean=after_mean,
                after_days=len(after),
                observed_monthly_delta=observed,
                status="observed" if after else "estimated_only",
            )
        )
    return RoiReport(
        method=ROI_METHOD,
        note=(
            "rows with no post-decision days report estimated_only — the "
            "mock window ends before decisions happen; live data closes this"
        ),
        rows=rows,
    )


def _pct_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


@router.get("/costs/trend")
def cost_trend(
    window_days: int = Query(
        7,
        ge=1,
        le=30,
        description="Days per comparison window (current vs the one before it).",
    ),
) -> CostTrendReport:
    """Compare the last N days of spend against the N days before them."""
    dataset = load_dataset()
    series = build_daily_series(dataset["daily_costs"])
    dates = series["dates"]
    current = slice(max(0, len(dates) - window_days), len(dates))
    previous = slice(
        max(0, len(dates) - 2 * window_days), max(0, len(dates) - window_days)
    )
    current_days = len(dates[current])
    previous_days = len(dates[previous])
    # An honest comparison needs two windows of equal length: a 13-day
    # current window against a 1-day leftover would report a ~1700% spend
    # "increase" on flat data. Unequal windows publish their totals and day
    # counts but no change figures.
    comparable = current_days > 0 and previous_days == current_days

    def window_totals(values: list[float]) -> tuple[float, float]:
        return round(sum(values[current]), 2), round(sum(values[previous]), 2)

    def compare(cur: float, prev: float) -> tuple[float | None, float | None, str]:
        if not comparable:
            return None, None, "insufficient_history"
        direction = "up" if cur > prev else "down" if cur < prev else "flat"
        return round(cur - prev, 2), _pct_change(cur, prev), direction

    current_total, previous_total = window_totals(series["totals"])
    total_change, total_pct, _ = compare(current_total, previous_total)
    rows = []
    for svc in series["services"]:
        cur, prev = window_totals(svc.values)
        change, change_pct, direction = compare(cur, prev)
        rows.append(
            ServiceTrendRow(
                service=svc.service,
                current_window_total=cur,
                previous_window_total=prev,
                change=change,
                change_pct=change_pct,
                direction=direction,
            )
        )
    if comparable:
        # Top movers first: the operator reads this list to answer "what changed".
        rows.sort(key=lambda r: abs(r.change), reverse=True)

    return CostTrendReport(
        currency=dataset["currency"],
        period=dataset["period"],
        window_days=window_days,
        current_window_days=current_days,
        previous_window_days=previous_days,
        dates=dates,
        totals=series["totals"],
        current_window_total=current_total,
        previous_window_total=previous_total,
        change=total_change,
        change_pct=total_pct,
        services=rows,
    )
