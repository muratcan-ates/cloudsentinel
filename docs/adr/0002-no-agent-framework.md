# ADR 0002 — The agent loop is written, not imported

**Status:** accepted · **Date:** 2026-08-02
**Scope:** `app/analyst.py`, `app/recommender.py`, `app/debate.py`, `app/pulse.py`

## Context

CloudSentinel runs a multi-agent chain: reflex → analyst → decision memory
→ recommender → skeptic or review panel → human. That is precisely the
shape LangGraph, PydanticAI, CrewAI and the Microsoft Agent Framework
exist to express, and using one would be the conventional choice.

## Decision

The orchestration is ~200 lines of ordinary Python in `pulse.py` and
`recommender.py`. No agent framework is a dependency.

## Why

**The interesting parts are the ones a framework does not give you.**
What makes this chain defensible is not that it has nodes and edges. It is
the escalation ladder (a stakes-aware confidence bar, so a BOLD stance on a
critical signal cannot wave itself past the skeptic), the per-pulse call
budget that degrades every agent to a deterministic fallback instead of
failing, the frozen evidence window that makes an analysis reproducible
months later, the numeric post-check on the final narrative, and the rule
that a panel seat which fails **abstains** rather than voting. Each of
those is a decision about *this* problem. In a framework they would be
custom nodes and custom callbacks anyway — the same code, with a graph API
wrapped around it.

**Runtime dependencies are the deployment risk.** The competition rule this
project actually operates under is that `requirements.txt` is frozen: a
juror clones the repo, runs `make setup`, and it works. Every runtime
package is a chance for a resolver conflict on someone else's machine and a
larger surface on a free-tier host with a cold start budget. An agent
framework is a large, fast-moving dependency that pulls its own.

**"Dış kod" is a review liability.** Where a framework decides the retry
policy, the prompt assembly or the tool-call loop, the honest answer to
"why does it do that?" is "the library does that". Every behaviour here has
an answer in this repo.

## Consequences

- Orchestration must be tested directly, and it is: the ladder, the budget
  exhaustion path, abstentions, quorum, cache partitioning and the
  replay/idempotency behaviour all have their own tests. A framework would
  have supplied confidence in its own machinery, not in ours.
- No graph visualisation for free. The observability was built instead —
  the per-hop orchestration trace with source, model, measured duration,
  confidence and named uncertainty per agent, plus the per-pulse run
  receipt. That is more useful than a static diagram, because it reports
  what the run *did* rather than what the code *could* do.
- Reimplementation cost is real but bounded, and it was paid once.
- **This decision does not scale forever.** It is right for one process,
  one chain and a fixed set of agents. Durable multi-step workflows,
  human-in-the-loop resumption across restarts, or a fan-out of dozens of
  agent types would justify revisiting it — and the seam is clean, because
  every agent is a plain function over a prompt and a Pydantic schema.

## Alternatives rejected

- **LangGraph / MAF / PydanticAI.** See above; a large runtime dependency
  bought mostly to express control flow this codebase already expresses.
- **A homegrown mini-framework.** Abstraction with one caller is a cost
  with no payer.
