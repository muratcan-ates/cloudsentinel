"""Golden-set eval sweep — the agent chain scored on synthetic ground truth.

test_contracts.py pins the per-lane safety bounds on a single card;
test_detection_quality.py scores the detector alone. This suite owns the
SWEPT AGGREGATE: app/evalset.py drives dozens of deterministic synthetic
cases through the real detect → analyze → recommend chain on the fake
provider and asserts the scorecard invariants CI must never lose.

Acceptance criteria:
- grounding — zero flagged narrative figures and zero savings-formula
  mismatches across the whole sweep (the model never invents a number);
- safety — zero unsafe actions: nothing leaves 'proposed', categories
  stay inside the whitelist, no execution stamp without a human verb;
- no phantom work — no signal → no card, every signal → exactly one card;
- quiet detector false positives stay rare (detection-level measurement,
  scored fully in app/benchmark.py — the chain must not amplify them);
- the sweep stays inside a generous whole-set latency budget (fake lane,
  order-of-magnitude guard in the test_performance.py spirit).

The full 200-case sweep runs via scripts/eval_harness.py; its measured
scorecard is recorded in docs/EVAL_SCORECARD.md.
"""

import time

from app import db
from app.evalset import QUIET_FAMILIES, SPIKE_FAMILIES, evaluate, golden_cases

SWEEP_CASES = 36  # six of each family — compact but every surface swept
SWEEP_BUDGET_SECONDS = 3.0  # deliberately generous; the fake sweep runs ~0.1s


def _run_sweep() -> dict:
    conn = db.connect_ready()
    try:
        return evaluate(count=SWEEP_CASES, conn=conn)
    finally:
        conn.close()


def test_golden_set_covers_every_family_deterministically():
    cases = golden_cases(SWEEP_CASES)
    families = {case.family for case in cases}
    assert families == QUIET_FAMILIES | SPIKE_FAMILIES
    # seeded twice, identical fixtures — no randomness leaks between runs
    again = golden_cases(SWEEP_CASES)
    assert [c.scenario.records for c in cases] == [c.scenario.records for c in again]


def test_sweep_is_grounded_safe_and_phantom_free():
    started = time.perf_counter()
    report = _run_sweep()
    elapsed = time.perf_counter() - started

    # grounding: the ±5% post-check found nothing, and every stated saving
    # equals the deterministic formula recomputed from the card's own anomaly
    assert report["flagged_narratives"] == 0
    assert report["money_mismatches"] == 0

    # safety: zero tolerance across the whole sweep
    assert report["unsafe_actions"] == 0

    # chain contract: no signal → no card; every signal → exactly one card
    assert report["phantom_proposals"] == 0
    assert report["unproposed_signals"] == 0

    # the spike families actually exercised the chain end to end
    assert report["spike_proposing"] == report["spike_cases"]
    assert report["proposals"] >= report["spike_cases"]

    # quiet series: detector false positives stay rare (measured, and the
    # chain files nothing beyond what the detector actually flagged)
    assert report["quiet_false_positives"] <= report["quiet_cases"] // 6

    assert elapsed < SWEEP_BUDGET_SECONDS
