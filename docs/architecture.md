# CloudSentinel — Architecture & Agent Design

This document describes the target architecture for Sprints 2–3. Sprint 1
deliberately shipped only the deterministic detection slice; everything below
builds on that foundation without rewriting it.

## System Overview

```mermaid
flowchart LR
    DS[Data Sources<br/>Sprint 1: mock cost JSON<br/>Sprint 3: mock security events] --> DET

    subgraph Detection Layer
        DET[Statistical Detector<br/>z-score per service<br/>deterministic, testable]
    end

    DET -->|anomaly records| ORCH

    subgraph Agent Layer
        ORCH[Orchestrator<br/>routes anomalies through agents] --> AN
        AN[Analyst Agent - Gemini<br/>explains the anomaly,<br/>assesses impact and likely cause] --> REC
        REC[Recommender Agent - Gemini<br/>proposes concrete remediation actions<br/>with risk level]
        MEM[(Decision Memory<br/>past anomalies and operator verdicts)]
        ORCH <--> MEM
    end

    REC -->|proposed action| HITL

    subgraph Human-in-the-Loop
        HITL[Approval Gate<br/>operator approves / rejects] --> EXEC[Action Log<br/>simulated execution + audit trail]
    end
```

## Design Principles

1. **Deterministic core, agentic reasoning on top.** Detection stays
   statistical and unit-tested; LLM agents interpret and recommend but never
   silently act. This keeps the demo reliable and the AI layer honest.
2. **Human-in-the-loop is a state machine, not a checkbox.** Every proposed
   action has a lifecycle: `proposed → approved | rejected → executed
   (simulated)`. Each transition is persisted with timestamp and actor.
3. **Memory makes agents purposeful.** Operator verdicts are stored and fed
   back into the Recommender's context, so repeated anomaly patterns get
   better recommendations over time — agent memory serving the product goal,
   not decoration.
4. **Same pipeline for cost and security.** Sprint 3's security signals (e.g.
   failed-login bursts, IAM policy changes) enter as another record type and
   flow through the identical detect → analyse → recommend → approve chain.

## Agent Roles (Sprint 2)

| Agent | Model | Responsibility | Input | Output |
|---|---|---|---|---|
| Analyst | Gemini | Triage the anomaly (REAL / SEASONAL / DATA_ERROR / KNOWN_CHANGE) with cited evidence and confidence; self-reflects on critical signals | anomaly record + service history | structured analysis |
| Recommender | Gemini | Propose exactly two options (cautious / bold) with risk and rollback; savings computed deterministically in Python | analysis + decision memory | one proposed action |
| Skeptic (debate-lite) | Gemini | Challenge low-confidence or contested recommendations — at most one extra call per decision; transcript kept | draft recommendation + analysis | verdict + final stance |
| Orchestrator | code (deterministic) | Route anomaly → analysis → recommendation → approval; retries, timeouts | anomaly records | tracked action proposals |

## API Evolution

| Sprint | Endpoint | Purpose |
|---|---|---|
| 1 (done) | `GET /anomalies` | Detect anomalies over cost data (z-score); persists each signal as an event with a stable id |
| 2 (done) | `POST /anomalies/{id}/analyze` | Run the Analyst agent (triage + evidence + confidence, reflection at critical z) |
| 2 (done) | `POST /anomalies/{id}/recommend` | Run the Recommender (+ Skeptic debate-lite) and file a proposed action |
| 2 (done) | `GET /actions` · `POST /actions/{id}/approve` · `POST /actions/{id}/reject` | Human-in-the-loop action lifecycle |
| 3 | security event ingestion + dashboard + deployment | Extend the same pipeline; live demo |

## Technology Decisions

- **FastAPI + Python** (bootcamp requirement), **Gemini** for the LLM layer.
- **pip + venv**, pinned `requirements.txt`.
- Persistence starts as a simple JSON/SQLite action store in Sprint 2 —
  enough for the HITL state machine without infrastructure overhead.
- Containerized via the repo `Dockerfile`; deployment target decided in
  Sprint 3.
