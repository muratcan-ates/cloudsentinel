# Agent-Chain Eval Scorecard — measured, not narrated

*A 200-case golden set of deterministic synthetic scenarios (ground truth
known by construction) swept through the real detect → analyze → recommend
chain. Runner: [`app/evalset.py`](../app/evalset.py) ·
`scripts/eval_harness.py` reproduces the table below in seconds ·
[`tests/test_llm_eval.py`](../tests/test_llm_eval.py) asserts the same
invariants in CI on every push.*

**Honest frame:** everything here is measured on the **deterministic
provider** — it scores the *pipeline's contract* (grounding checks, safety
gates, HITL state machine), not live-model quality. The live-model eval set
(triage accuracy, evidence precision, unsupported-claim rate, P95 latency
against real Gemini) remains roadmap work — Sprint 3 backlog **B8**.

## Golden-set results (200 cases, threshold 2.0)

| Metric | Result | Meaning |
|---|---|---|
| Signals detected → proposals filed | **143 → 143** | one card per signal — no phantom work, no orphaned signal |
| Flagged narrative figures (±5% post-check) | **0** | no money figure in any narrative diverged from the computed arithmetic |
| Savings-formula mismatches | **0** | every stated saving equals the deterministic Python projection exactly |
| Unsafe actions | **0** | nothing left `proposed` without a human verb; every category inside the whitelist; no execution stamp appeared |
| Quiet cases (no planted anomaly) | 66, **7 detector false positives** | the 7 are detection-level statistics (small-window variance), scored fully by the detection benchmark — the chain filed nothing beyond what the detector flagged |
| Spike cases producing a proposal | **134 / 134** | every planted anomaly ended as a decidable card |
| Chain latency per case (mean / p95 / max) | **0.83 / 1.52 / 11.0 ms** | fake lane — the pipeline's own overhead, excluding model time |

Case families: clean spike · critical spike · downward collapse · weekend
seasonal spike · quiet · borderline (six families, seeded — two runs give
byte-identical fixtures).

## Guardrails behind these numbers

| Guardrail | Where enforced | Verified by |
|---|---|---|
| Money is computed, never generated (±5% narrative post-check) | `app/recommender.py` `verify_narrative_figures` | golden set (0 flagged) + `tests/test_recommender.py` |
| Confidence stays a real probability [0, 1] | LLM schema + contract suite | `tests/test_contracts.py`, `tests/test_analyst.py` |
| Every option states a rollback | required pydantic field | `tests/test_contracts.py` |
| Execute requires prior approval (409 otherwise) | HITL state machine, `app/actions.py` | `tests/test_actions.py` |
| Read-only demo blocks every write (403) | middleware, `main.py` | `tests/test_demo_ops.py`, `tests/test_llm_contracts.py` |
| Per-pulse LLM call budget + hard per-call timeout | `app/llm.py` · budget observable on the fake lane too | `tests/test_guardrails.py`, `tests/test_llm_contracts.py` |
| Budget exhaustion degrades to rule-based fallbacks, honestly labeled | `generate_with_fallback`, source=`fallback` | `tests/test_llm.py`, `tests/test_contracts.py` |
| Learning loop proposes only — adoption is a human decision; no apply path exists, by design | `app/reflex.py`, `app/insights.py` | `tests/test_reflex.py`, `tests/test_insights.py` |

## What we would measure in production

The backlog's live-model eval (B8) names the metrics this scorecard
deliberately does not claim: triage accuracy against labeled scenarios,
evidence precision, unsupported-claim rate, format-failure rate, P95
latency and cost per anomaly on the live provider. Until that runs, the
honest label stands: *a well-engineered prototype that faithfully simulates
a production product's behavior* — with its pipeline contract now measured,
not narrated.
