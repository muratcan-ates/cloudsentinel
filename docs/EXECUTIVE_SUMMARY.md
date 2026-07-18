# CloudSentinel — Executive Summary

*YZTA Bootcamp 2026 · AI Track · Team CloudSentinel (Group 60)*
*One-page brief for the submission and the jury. SCQA framing. Every claim
here is one the codebase can back up — including the boundaries.*

---

**Situation.** Cloud cost and security signals arrive faster than any team
can triage them, and the industry's own numbers are blunt: analysts project
that a large share of agentic-AI initiatives get cancelled over runaway,
unaccountable cost. The pressure is to *act* on anomalies quickly.

**Complication.** The two obvious answers both fail. A passive dashboard
sees the spike but decides nothing. A fully-autonomous agent decides fast
but acts on real infrastructure with no accountable human in the loop — and
no serious operator will trust it. Speed and accountability pull apart.

**Question.** How do you get the *speed* of automation and the
*accountability* of a human decision at the same time?

**Answer — CloudSentinel.** A dual-loop, agentic decision-support system for
cloud operations. A deterministic **reflex** lane handles routine anomalies
in measured sub-millisecond statistics; a **conscious** loop escalates the
hard calls — an Analyst triages with cited evidence, a Skeptic challenges
weak reasoning, and a Recommender proposes a cautious and a bold option with
Python-computed savings and rollback. **Nothing executes without a human.**
The machine watches, the human decides.

---

## What it actually does

- **Detects** cost, security, and payment anomalies on one deterministic
  detection line (rolling-baseline z-score / MAD, optional weekly
  seasonality), driven by declarative **mission YAML** — change the mission,
  change the behavior, same engine.
- **Reasons** with a multi-agent arena whose money figures are computed in
  Python and never generated, then verified by a ±5% numeric post-check;
  the reflex latency is **measured**, not claimed.
- **Decides** through a human-in-the-loop lifecycle
  (`proposed → approved/rejected → executed`) with recorded rationale and an
  append-only decision ledger exportable to CSV.
- **Remembers** operator verdicts and feeds them back into future
  recommendations; a learning loop *proposes* new reflex rules from that
  memory but never applies one automatically (human-in-the-loop stays
  sacred).
- **Accounts** for its own operation — a self-FinOps view tracks the
  system's own LLM spend, and analytics turn history into a HITL funnel,
  approved-savings, forecast and ROI figures.

## Why it is credible

Built on FastAPI + Python 3.12 with a Gemini provider abstraction that
degrades honestly to a deterministic fake and a rule-based fallback, so the
full demo runs offline with no quota gamble. Quality bar: **400+ automated
tests** over 30+ endpoints, ruff-clean, CI on every push and PR, a
13-step live smoke sweep, and a benchmark harness that measures rather than
asserts.

## Boundaries we state, not hide

Data is synthetic and execution is **simulated by design** for the
competition; there is no authentication, sqlite rather than Postgres, no
background scheduler, and the fraud lane is published deterministic
arithmetic, not ML. These are documented decisions in `Scope & Limitations`
and the Sprint 3 backlog — the honest label is *a well-engineered prototype
that faithfully simulates a production product's behavior*. The roadmap to
close that gap (real identity, durable state, scheduled ingestion, one real
side effect, post-change verification) is written down, prioritized, and
deliberately left for after the competition window.

---

**In one line:** CloudSentinel closes the gap between *"your cloud bill
spiked"* and *"someone accountable did something about it"* — fast where it
can be, human where it must be.
