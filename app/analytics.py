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

import json
import sqlite3

from fastapi import APIRouter, Depends, Query

from app import db
from app.actions import TIMEOUT_ACTOR, expire_stale_proposals
from app.detection import build_daily_series, load_dataset
from app.models import (
    AgentTelemetry,
    CostTrendReport,
    DecisionAnalyticsReport,
    DecisionQuality,
    HitlFunnel,
    ServiceTrendRow,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])

SAVINGS_METHOD = (
    "sum of the preferred option's deterministic monthly projection "
    "over approved and executed actions"
)


def _funnel(conn: sqlite3.Connection) -> HitlFunnel:
    signals = conn.execute("SELECT count(*) FROM events").fetchone()[0]
    analyzed = conn.execute(
        "SELECT count(*) FROM events WHERE analysis_json IS NOT NULL"
    ).fetchone()[0]
    states = {
        row["state"]: row["n"]
        for row in conn.execute("SELECT state, count(*) AS n FROM actions GROUP BY state")
    }
    timeout_rejections = conn.execute(
        "SELECT count(*) FROM actions WHERE decided_by = ?", (TIMEOUT_ACTOR,)
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
        "SELECT analysis_json FROM events WHERE analysis_json IS NOT NULL"
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
    return AgentTelemetry(
        triage_distribution=dict(sorted(triage.items())),
        avg_confidence=round(sum(scores) / len(scores), 4) if scores else None,
        requests_total=sum(by_agent.values()),
        cache_hits=cache_hits,
        by_source=by_source,
        by_agent=by_agent,
        debates=by_agent.get("skeptic", 0),
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
