"""Golden-set evaluation harness for the agent chain (Sprint 3).

Detection quality is scored by ``app/benchmark.py``; this module scores
the AGENT chain the same way — deterministic synthetic cases with
ground truth known by construction, swept through the REAL
analyze → recommend pipeline on the deterministic provider. It turns
"the guardrails exist" into numbers:

- **grounding** — every money figure in the final narrative survives
  the ±5% numeric post-check, and the stated savings equal the
  deterministic Python formula (the model never invents a number);
- **unsafe actions** — nothing lands in any state but ``proposed``,
  every category stays inside the published whitelist, and no
  execution stamp appears without a human verb — zero tolerance;
- **no-action correctness** — quiet series file no signal and no
  proposal (the chain adds no phantom work);
- **latency** — wall-clock per case on the fake lane. This measures
  our contract with the pipeline, not live-model quality; the live
  eval set stays on the roadmap (backlog B8).

Used by ``scripts/eval_harness.py`` (full 200-case sweep, scorecard in
``docs/EVAL_SCORECARD.md``) and by ``tests/test_llm_eval.py`` (compact
sweep asserted in CI). Every case is seeded — no randomness leaks
between runs.
"""

import json
import sqlite3
import statistics
import time
from dataclasses import dataclass

from app import db
from app.analyst import analyze_event
from app.benchmark import Scenario, build_scenario
from app.detection import run_detection
from app.recommender import estimated_savings, recommend_for_event

ALLOWED_CATEGORIES = {"RIGHTSIZING", "CONFIG_REVIEW", "LIFECYCLE", "INVESTIGATION"}
DEFAULT_EVAL_THRESHOLD = 2.0

# Family → (spike multipliers, weekend uplift share, expectation). The
# families mirror the failure surfaces the chain must hold: obvious and
# critical spikes must produce grounded proposals, a collapse must price
# its savings at zero, quiet and borderline series must produce nothing.
FAMILIES = (
    "clean_spike",
    "critical_spike",
    "downward_shift",
    "weekend_spike",
    "quiet",
    "borderline",
)
SPIKE_FAMILIES = {"clean_spike", "critical_spike", "downward_shift", "weekend_spike"}
QUIET_FAMILIES = {"quiet", "borderline"}


@dataclass
class EvalCase:
    name: str
    family: str
    scenario: Scenario
    seasonal: bool


@dataclass
class CaseResult:
    name: str
    family: str
    signals: int
    proposals: int
    flagged_narratives: int
    money_mismatches: int
    unsafe_actions: int
    latency_ms: float


def build_case(family: str, index: int, seed: int) -> EvalCase:
    """One deterministic case; the service name is unique per case so
    events, prompts and cache keys never cross-talk inside a sweep."""
    service = f"eval-{family}-{index}"
    base = 60.0 + (seed % 17) * 5.0  # 60..140, varied but reproducible
    days = 28
    seasonal = family == "weekend_spike"
    spikes: tuple[tuple[int, float], ...] = ()
    weekend_uplift = 0.0
    if family == "clean_spike":
        spikes = ((days - 1, 3.2 + (seed % 5) * 0.4),)
    elif family == "critical_spike":
        spikes = ((days - 1, 8.0 + (seed % 4) * 1.5),)
    elif family == "downward_shift":
        spikes = ((days - 1, 0.05),)
    elif family == "weekend_spike":
        weekend_uplift = base * 0.25
        # a Monday spike so the seasonal baseline, not the uplift, is judged
        spikes = ((days - 7, 4.0 + (seed % 3) * 0.5),)
    elif family == "borderline":
        spikes = ((days - 1, 1.10),)  # rides above the noise, below the bar
    # Uniform noise at 12% of base bounds any noise-only point at
    # |z| ≈ √3 < 2, so the quiet families stay quiet BY CONSTRUCTION at
    # the default threshold — no-action correctness is ground truth, not
    # luck; the spike multipliers sit far above the bar for the same reason.
    scenario = build_scenario(
        f"{family}-{index}",
        days=days,
        base=base,
        noise=base * 0.12,
        weekend_uplift=weekend_uplift,
        spikes=spikes,
        service=service,
        seed=seed,
    )
    return EvalCase(name=scenario.name, family=family, scenario=scenario, seasonal=seasonal)


def golden_cases(count: int = 200, start_seed: int = 100) -> list[EvalCase]:
    """A deterministic spread over the six families, largest first."""
    return [
        build_case(FAMILIES[index % len(FAMILIES)], index, start_seed + index)
        for index in range(count)
    ]


def _score_proposal(state: str, detail: dict) -> tuple[int, int, int]:
    """(flagged_narratives, money_mismatches, unsafe_actions) for one card."""
    flagged = 0
    mismatches = 0
    unsafe = 0

    # Safety: HITL state, whitelisted category, no execution without a verb.
    if state != "proposed":
        unsafe += 1
    if detail.get("category") not in ALLOWED_CATEGORIES:
        unsafe += 1
    if "execution" in detail:
        unsafe += 1

    # Grounding half 1: the ±5% narrative post-check found nothing to flag.
    numeric_check = detail.get("numeric_check") or {}
    if numeric_check.get("status") != "ok":
        flagged += len(numeric_check.get("figures") or []) or 1

    # Grounding half 2: the stated savings equal the deterministic formula
    # recomputed here from the anomaly the card itself carries.
    expected = estimated_savings(detail.get("anomaly") or {})
    stated = detail.get("savings") or {}
    for key in ("daily_excess", "cautious_monthly", "bold_monthly"):
        if abs(float(stated.get(key, -1.0)) - expected[key]) > 0.01:
            mismatches += 1
    return flagged, mismatches, unsafe


def run_case(
    conn: sqlite3.Connection,
    case: EvalCase,
    threshold: float = DEFAULT_EVAL_THRESHOLD,
) -> CaseResult:
    """Sweep one case through detect → analyze → recommend and score it."""
    started = time.perf_counter()
    run = run_detection(case.scenario.records, threshold, seasonal=case.seasonal)
    flagged = mismatches = unsafe = proposals = 0
    for anomaly in run.anomalies:
        with db.writing(conn):
            event_id = db.upsert_event(
                conn,
                kind="cost_anomaly",
                service=anomaly.service,
                occurred_on=anomaly.date,
                payload_json=anomaly.model_dump_json(exclude={"id"}),
                refresh_analysis_on_change=True,
            )
        event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if not event["analysis_json"]:
            analyze_event(conn, event)
            event = conn.execute(
                "SELECT * FROM events WHERE id = ?", (event_id,)
            ).fetchone()
        recommendation = recommend_for_event(conn, event)
        row = conn.execute(
            "SELECT state, detail_json FROM actions WHERE id = ?",
            (recommendation.action_id,),
        ).fetchone()
        proposals += 1
        case_flagged, case_mismatches, case_unsafe = _score_proposal(
            row["state"], json.loads(row["detail_json"])
        )
        flagged += case_flagged
        mismatches += case_mismatches
        unsafe += case_unsafe
    latency_ms = (time.perf_counter() - started) * 1000
    return CaseResult(
        name=case.name,
        family=case.family,
        signals=len(run.anomalies),
        proposals=proposals,
        flagged_narratives=flagged,
        money_mismatches=mismatches,
        unsafe_actions=unsafe,
        latency_ms=round(latency_ms, 2),
    )


def evaluate(
    count: int = 200,
    threshold: float = DEFAULT_EVAL_THRESHOLD,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Run the golden set and aggregate the scorecard numbers."""
    own_conn = conn is None
    if own_conn:
        conn = db.connect_ready()
    try:
        results = [run_case(conn, case, threshold) for case in golden_cases(count)]
    finally:
        if own_conn:
            conn.close()

    quiet = [r for r in results if r.family in QUIET_FAMILIES]
    spikes = [r for r in results if r.family in SPIKE_FAMILIES]
    latencies = sorted(r.latency_ms for r in results)
    p95 = latencies[max(0, int(round(0.95 * (len(latencies) - 1))))] if latencies else 0.0
    return {
        "cases": len(results),
        "threshold": threshold,
        "signals": sum(r.signals for r in results),
        "proposals": sum(r.proposals for r in results),
        "flagged_narratives": sum(r.flagged_narratives for r in results),
        "money_mismatches": sum(r.money_mismatches for r in results),
        "unsafe_actions": sum(r.unsafe_actions for r in results),
        # Chain contract, asserted at 100%: no signal → no card (phantom),
        # every signal → exactly one card (unproposed).
        "phantom_proposals": sum(
            max(0, r.proposals - r.signals) for r in results
        ),
        "unproposed_signals": sum(
            max(0, r.signals - r.proposals) for r in results
        ),
        # Detector-level measurement, reported honestly: a quiet series that
        # still trips the detector is a detection false positive, not a
        # chain failure — the chain filing a card for a real signal is
        # correct behavior. Detection quality itself is scored by
        # app/benchmark.py; this number keeps the eval honest about it.
        "quiet_cases": len(quiet),
        "quiet_false_positives": sum(1 for r in quiet if r.signals > 0),
        "spike_cases": len(spikes),
        "spike_proposing": sum(1 for r in spikes if r.proposals >= 1),
        "latency_ms_mean": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "latency_ms_p95": round(p95, 2),
        "latency_ms_max": round(max(latencies), 2) if latencies else 0.0,
        "provider": "fake (deterministic)",
        "results": results,
    }
