# ADR 0005 — The fake provider is a first-class lane, and the demo runs on it

**Status:** accepted · **Date:** 2026-08-02 · **Scope:** `app/llm.py`

## Context

Every agent in the chain can be served by one of three sources: `gemini`
(a live model), `fake` (a deterministic in-process composer), or
`fallback` (a rule-based answer when no model could be reached).

The deployed showcase and the recorded demo run on `fake`. That looks, at
first glance, like the weakest possible admission — so it is worth writing
down why it is the correct configuration rather than a shortfall.

## Decision

`FakeProvider` is a supported lane, not test scaffolding:

- It is **schema-driven**. Each agent registers a composer against its own
  Pydantic response schema, so the fake returns a genuinely valid object of
  the same type the live model must return. A schema drift breaks the fake
  first.
- It is **context-aware**. The demo analyst narrates the actual anomaly —
  the real service, the real cost against the real baseline, the real
  z-score — and the demo review panel's three charters produce **real
  dissent** as a pure function of the draft they received. Nothing is
  rubber-stamped and nothing is random.
- It is **honestly labelled** everywhere: `source: "fake"` on the card, in
  the AI usage ledger, on `/health`, and as the `simulated_provider`
  uncertainty source on every agent's judgement.
- Its confidence is a **deliberate 0.5**, with a rationale that says why.
  It is not tuned upward to look good, and because 0.5 sits below the
  deliberation bar, the demo run genuinely escalates to the review panel
  rather than skipping it.

## Why the demo runs on it

**Determinism.** A demo has one take. A live model can be slow, rate-limited
or differently worded on the take that gets recorded.

**Quota is a shared, exhaustible resource.** The free tier grants a daily
request budget on one key. A rehearsal loop that burns it leaves nothing for
the actual demo — which is the same reason the per-pulse call budget exists.

**Zero cost is a design property, not a claim.** The project is
billing-disabled by construction. Running the showcase on a lane that
spends nothing keeps that true rather than asserted.

**It proves the fallback path works.** A system whose deterministic lane is
exercised on every run is a system whose degradation path is not
theoretical. The live lane's failure mode is *this* lane.

## Consequences

- The demo shows real orchestration — escalation, panel dissent, an
  overruled stance — without a live call. That is a genuine property of the
  chain, not a scripted animation.
- **The cost, stated plainly:** the recorded demo does not demonstrate live
  model quality. It demonstrates the pipeline, the guardrails and the
  decision surface. Flipping `SENTINEL_FAKE_LLM=0` with a key present
  switches every agent to Gemini with no other change, and the provenance
  labels flip with it.
- Because the fake is deterministic, the LLM cache and the whole
  idempotency story stay testable end to end.
- A reader who mistakes `fake` for "mock data" is corrected by the surfaces
  themselves: the data-source badge and the provider badge are separate,
  runtime-derived, and never hardcoded.

## Alternatives rejected

- **Record the demo live.** One take, a shared quota, and a model that may
  phrase itself differently on the take that counts.
- **Ship a fake that always agrees.** It would make the panel look
  decorative — the opposite of the thing being demonstrated.
- **Hide the lane.** Every honesty rule in this codebase points the other
  way: label the source, then argue for it.
