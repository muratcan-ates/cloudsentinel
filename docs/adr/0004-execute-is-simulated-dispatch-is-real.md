# ADR 0004 — Execute is simulated; the dispatch that carries it is real

**Status:** accepted · **Date:** 2026-08-02
**Scope:** `app/actions.py`, `app/dispatch.py`

## Context

An approved action says something like "right-size the compute tier" or
"lower the read-replica class". Executing it for real means holding cloud
credentials with mutating permissions and calling a provider API that
changes someone's infrastructure.

Nothing in the demo estate is real: the cost data is a fixture (or this
app's own request telemetry), the services are named `compute`, `database`,
`storage`, `network`. There is no tier to resize.

The lazy version of this decision is to leave `POST /execute` as a stub,
say nothing, and let a reader assume it works. That is the version worth
refusing.

## Decision

Two halves, drawn deliberately in different places.

**Infrastructure mutation is SIMULATION.** Executing an approved action
advances the state machine, stamps `executed_at`, appends an `executed`
transition to the append-only trail, seals it into the audit chain, and
renders the incident report. It calls no cloud API. The response and every
surface that shows it are labelled `SIMULATION` — the word ships in the
payload, not only in the docs.

**Delivery is REAL.** When `SENTINEL_EXECUTE_WEBHOOK_URL` is configured,
executing also POSTs the incident — decision, computed savings, Markdown
report — to the operator's own endpoint (Discord, Slack, n8n, anything that
takes a JSON POST). That request genuinely leaves the process.

So the boundary is: **the decision record really ships; the infrastructure
really does not.**

## Why the line is there

The valuable and reviewable part of this product is the decision pipeline —
detection, deliberation, the human verdict, the audit trail. Wiring a real
cloud mutation would add credential handling and blast radius while proving
nothing about that pipeline. Meanwhile, an incident that never leaves the
building proves nothing about integration, which is why delivery is real:
it exercises retries, timeouts, the SSRF guard on the target, and the
discipline of never doing network I/O inside an open transaction.

## Consequences

- The dispatch path carries the security surface, and it is guarded there:
  the target passes `netguard` (no loopback, link-local, private ranges or
  cloud metadata addresses), the URL may embed a secret so only its **host**
  is ever logged or stored, and a failed delivery never fails the execute —
  the state machine must not depend on a webhook's mood, and the failed
  note is the honest record.
- Transaction discipline is load-bearing: the execute transaction commits
  first, delivery runs after it, and the outcome joins the action's detail
  in a second small transaction (`app/db.py`'s locked rule — never network
  inside an open transaction).
- Making mutation real later changes one function, not the architecture.
  The state machine, approvals, trail and chain already treat "executed" as
  a terminal state with an actor and a timestamp.
- **The cost:** the ROI panel can only ever report *estimated* savings for
  simulated executions, and it says `estimated_only` rather than inventing
  an observation.

## Alternatives rejected

- **Real cloud mutation.** Credentials with write scope on a bootcamp
  project, for a fixture estate that does not exist.
- **Simulate the webhook too.** Then nothing about the integration is
  tested, and "it would work in production" becomes an untested claim.
- **Say nothing.** Silence here reads as a hidden gap. Naming it is the
  point of the ADR.
