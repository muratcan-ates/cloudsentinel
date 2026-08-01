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

Quality bar: **1184 pytest cases** (fake provider, ~35 s), ruff clean, every
commit suite-green, CI on every push and PR.

## How to run it

```bash
make setup && make demo     # fake provider, fresh dates, demo reset armed
# then in another shell:
make smoke                  # 26-step PASS/FAIL sweep over the live chain
```

## Boundaries that are decisions, not gaps

Bundled datasets are the default and the demo runs on them — the live lanes
(self-telemetry, imported billing export, external JSON feeds) are env-gated
and off unless asked for, and no cloud-provider SDK or credential is involved
anywhere. Identity is local (`/auth`, PBKDF2, four roles), not OIDC/SSO, and
there is no tenant isolation. Storage is sqlite3 on an ephemeral disk, not
Postgres, so a restart clears history. The standing watchdog is opt-in;
otherwise scanning stays request-triggered. Execution is always simulated —
no real infrastructure is ever touched — and fraud scoring is published
deterministic arithmetic, not ML. Each boundary keeps the build honest and
demoable; the road out of them is section B of
[docs/sprint3_backlog.md](docs/sprint3_backlog.md), and the closeout plan is
[docs/CLOSEOUT_48H.md](docs/CLOSEOUT_48H.md).

Four of those boundaries were moved deliberately rather than quietly, and
each moved only as far as it could be defended:

- **The audit trail stops asking to be believed.** Every decision and
  lifecycle transition is sealed with the hash of the one before it, and
  `GET /audit/verify` walks the chain and names the first broken link.
  The ledger still lives on the ephemeral disk — the chain proves the
  history was not rewritten, it does not make the history survive a
  restart. That remains Postgres's job.
- **The safety knobs are audited at boot.** Read-only mode, the approver
  requirement, the provider choice and the outbound escape hatch are all
  environment variables that default to off, which is right for a laptop
  and silent everywhere else. Under `SENTINEL_ENV=production` a demo
  posture now refuses to boot; every other profile, this deployment
  included, is unchanged and only logs its findings.
- **Outbound targets are checked before the socket opens.** The feed and
  webhook URLs are configuration rather than user input today, but an
  unguarded fetch is a server-side request forgery waiting for the day
  they are not: https only, no loopback, private or link-local
  destination, and no redirect-following into one.
- **Execution is still simulated; delivery is not.** Approving an action
  mutates nothing in any cloud. What genuinely leaves the building is the
  incident record itself, to an operator-configured webhook, after the
  transaction commits — the honest half of an integration rather than a
  claimed one.
