# CloudSentinel — Executive Summary

*YZTA Bootcamp 2026 · AI Track · Team CloudSentinel (Group 60). Every number
below was measured in this repository, beside the command that reproduces it.*

---

**Situation.** Cloud cost, security and payment signals arrive faster than any
on-call team can triage them, and each one is a question about money.

**Complication.** The two obvious answers both fail. A dashboard sees the spike
and decides nothing. An autonomous agent decides quickly and acts on live
infrastructure with nobody accountable for the result. Speed and accountability
pull apart.

**Question.** How do you get the speed of automation and the accountability of a
human decision at the same time?

**Answer.** CloudSentinel splits the loop in two. A deterministic **reflex** lane
clears routine anomalies in a measured **0.24 ms mean, 0.31 ms p95** over 200
samples (`app/benchmark.py`). A **conscious** loop escalates the rest: an Analyst
cites evidence, a Skeptic attacks weak reasoning, a Recommender returns one
cautious and one bold option, each with a rollback, whose savings are computed in
Python and re-checked against its own narrative at ±5%. **Nothing executes
without a human.** The machine watches; the human decides.

## What the code shows

- **1317 tests pass**, 96% coverage over 5654 statements, ruff clean
  (`SENTINEL_FAKE_LLM=1 pytest -q`). **61 operations** across 59 paths are
  enrolled automatically by `tests/test_endpoint_matrix.py` — a route added
  tomorrow is tested the moment it is mounted.
- A **288-case golden set** (`scripts/eval_harness.py --cases 288`) swept through
  the real chain: **208 signals → 208 proposals**, 0 phantom cards, 0 orphaned
  signals, **0 unsafe actions**, 0 savings-formula mismatches; 7.46 ms mean /
  12.03 ms p95 per case.
- Adversarial families in that same sweep: **0 of 160 injected directives
  obeyed**, 0 prompt-boundary escapes, **32/32 planted numeric lies caught with
  0 false alarms**, and **32/32 abstentions** where history is too thin to have
  an opinion. Twelve of 64 quiet cases were detector false positives — stated,
  not smoothed.
- **Three missions, one engine**: security (1.75 / 14-day), fraud (2.75 /
  21-day), finops (2.0 / 28-day) are YAML in `configs/`. Change the mission,
  change the posture, not the code.
- The ledger does not ask to be believed. `GET /audit/verify` recomputes every
  SHA-256 link from genesis against the live rows and names which of four ways
  it broke: spliced, rewritten, source-modified, source-deleted.

## Real vs. simulated

| Real | Simulated |
|---|---|
| Decision record, operator identity, hash-chained audit trail | Any change to any cloud resource — always |
| Outbound webhook POST of the incident (`app/dispatch.py`) | The `executed` transition, stamped `SIMULATION` |
| Detection statistics, savings arithmetic, guardrails | The data: bundled synthetic fixtures by default |
| `/metrics`, `/audit/verify`, `/ops/preflight` | Live-model quality — scored on the deterministic lane |

## What we would build next

1. Live-model eval: triage accuracy and injection obedience against real Gemini.
2. Durable state and real identity — Postgres, OIDC, tenant isolation.
3. One genuine side effect, behind approval, with post-change verification.
