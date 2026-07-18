# PROJECT_CONTEXT — CloudSentinel

One page for a newcomer (teammate, juror, contributor) to hold the whole
project. Product depth lives in [README.md](README.md); design rationale in
[docs/architecture.md](docs/architecture.md).

## What this is

CloudSentinel is an **agentic decision-support system** for cloud
operations: it detects anomalies in cost and security data, has AI agents
reason about each signal and propose evidence-backed remediation options,
and leaves every critical decision to a human operator. The thesis in one
line: **the machine watches, the human decides.**

Built by **Team CloudSentinel (Group 60)** for the YZTA Bootcamp 2026 AI
track. All data is synthetic by design during the competition; execution is
always simulated.

## The product in five beats

1. **Detect** — a deterministic detector (rolling baseline, z-score or MAD,
   optional weekly seasonality) scores three mock lanes: cloud cost,
   security event counts and payment events (published rule score).
2. **Reason** — the Analyst triages each cost anomaly with cited evidence;
   the Recommender proposes a cautious and a bold option with risk and
   rollback; a Skeptic reviews contested calls; a Chronicler narrates each
   run. Every money figure is computed in Python, never generated.
3. **Decide** — proposals wait in a decision inbox; an operator approves or
   rejects with a recorded rationale. Nothing executes unapproved, and
   execution stays simulated.
4. **Remember** — verdicts become decision memory that future
   recommendations consume (and disclose); a hop-by-hop trace makes the
   chain's actual execution visible on every card.
5. **Account** — analytics turn the persisted history into a HITL funnel,
   approved savings, trend/forecast/what-if/ROI figures, and a self-FinOps
   view of the system's own LLM spend.

## Stack and shape

FastAPI + Python 3.12 · sqlite3 (WAL, seed-on-startup) · Gemini free tier
behind a provider abstraction with a deterministic fake provider and
rule-based fallbacks (the full demo runs offline) · vanilla-JS single-page
dashboard under a strict CSP (`script-src 'self'`, Swagger self-hosted) ·
pip + venv · Render (free tier) as the deploy target, Dockerfile as the
fallback.

Quality bar: **365 pytest cases** (fake provider, ~7 s), ruff clean, every
commit suite-green.

## How to run it

```bash
make setup && make demo     # fake provider, fresh dates, demo reset armed
# then in another shell:
make smoke                  # 13-step PASS/FAIL sweep over the live chain
```

## Boundaries that are decisions, not gaps

Mock data only, no real cloud adapters; no auth; sqlite3, not Postgres; no
background scheduler (request-triggered timeouts instead); fraud scoring is
published deterministic arithmetic, not ML. Each boundary keeps the
competition build honest and demoable; the post-competition roadmap in
[docs/architecture.md](docs/architecture.md) names what comes next.
