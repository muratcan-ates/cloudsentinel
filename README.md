<img src="docs/img/banner.png" alt="CloudSentinel — the machine watches, the human decides" width="100%" />

<div align="center">

# ☁️ CloudSentinel

### AI-agent powered cloud cost & security anomaly detection — with a human in the loop

**YZTA Bootcamp 2026 · AI Track · Group 60**

[Product](#information-about-the-product) · [Architecture](docs/architecture.md) · [How to Run](#how-to-run-local) · [Sprint 2](#sprint-2) · [Field Guide](#field-guide--sixty-seconds-to-a-decision)

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.128-009688?logo=fastapi&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-Sprint_2-8E75B2?logo=googlegemini&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)
![Last Commit](https://img.shields.io/github/last-commit/muratcan-ates/cloudsentinel?style=flat-square)

</div>

## 📖 Table of Contents

- [Team Name](#team-name)
- [Information About the Product](#information-about-the-product)
  - [Team Members](#team-members)
  - [Product Name](#product-name) · [Product Description](#product-description) · [Product Features](#product-features) · [Does / Does Not](#what-it-does--what-it-deliberately-does-not) · [Target Audience](#target-audience)
  - [Three Roles, One Control Room](#three-roles-one-control-room) · [What Makes CloudSentinel Different](#what-makes-cloudsentinel-different)
  - [The System at a Glance](#the-system-at-a-glance) · [Repository Map](#repository-map)
  - [How to Run (Local)](#how-to-run-local)
  - [Built With](#built-with) · [Sprint 1 Deliverables](#project-status--sprint-1-deliverables) · [Sprint 2 Progress](#project-status--sprint-2-progress) · [Roadmap](#roadmap-sprint-2-3)
  - [Requirements Compliance](#requirements-compliance) · [Scope & Limitations](#scope--limitations-by-design)
  - [Product Backlog URL](#product-backlog-url)
- [Sprint 1](#sprint-1) · [Sprint 2](#sprint-2) · [Sprint 3](#sprint-3)
- [Field Guide](#field-guide--sixty-seconds-to-a-decision) · [In Short](#in-short) · [Acknowledgements](#acknowledgements)

# Team Name

Group 60 – Team CloudSentinel

# Information About the Product

## Team Members

<table align="center">
<tr>
<td align="center">
  <a href="https://github.com/tuanaydin">
    <img src="https://github.com/tuanaydin.png" width="90" alt="Tuana Aydın"/>
    <br/><sub><b>Tuana Aydın</b></sub>
  </a>
  <br/><sub>Product Owner</sub>
</td>
<td align="center">
  <a href="https://github.com/muratcan-ates">
    <img src="https://github.com/muratcan-ates.png" width="90" alt="Muratcan Ateş"/>
    <br/><sub><b>Muratcan Ateş</b></sub>
  </a>
  <br/><sub>Scrum Master</sub>
</td>
<td align="center">
  <a href="https://github.com/caglayurtsvn">
    <img src="https://github.com/caglayurtsvn.png" width="90" alt="Çağla Yurtseven"/>
    <br/><sub><b>Çağla Yurtseven</b></sub>
  </a>
  <br/><sub>Developer</sub>
</td>
<td align="center">
  <a href="https://github.com/mertefekurt">
    <img src="https://github.com/mertefekurt.png" width="90" alt="Mert Kurt"/>
    <br/><sub><b>Mert Kurt</b></sub>
  </a>
  <br/><sub>Developer</sub>
</td>
</tr>
</table>

## Product Name

CloudSentinel

## Product Description

CloudSentinel is an agentic decision-support system that monitors cloud cost and security data, detects anomalies in that data, generates action recommendations for detected anomalies through AI agents, and leaves the final approval of critical actions to a human operator (human-in-the-loop). The backend is FastAPI + Python; the LLM layer is built for Gemini behind a provider abstraction, with a deterministic fake provider that keeps every agent behavior testable and demo-able offline. At the MVP stage the system runs on synthetic (mock) data.

## Product Features

- Anomaly detection on cloud cost data (per-service z-score, live threshold control)
- **Analyst agent** — triages every anomaly (REAL / SEASONAL / DATA_ERROR / KNOWN_CHANGE) with cited evidence rows and a self-assessed confidence; self-reflects on critical signals
- **Recommender agent** — proposes exactly two options (cautious / bold) with risk and rollback plans; estimated savings are computed deterministically in Python, never by the model
- **Debate-lite skeptic** — low-confidence or contested recommendations get one extra adversarial review; the transcript ships with the proposal
- **Decision memory** — operator verdicts are stored and fed back into the Recommender's context, so repeated anomaly patterns meet an agent that remembers
- **Human-in-the-loop lifecycle** — `proposed → approved/rejected → executed (simulated)` with idempotent decisions, request-triggered timeouts and a full audit trail; nothing ever executes without a human
- **Pulse + Chronicler** — one call drives the whole chain (detect → analyze → debate → recommend → inbox) with a tagged JSON log stream; a chronicler agent narrates every run into an operator briefing, and the last run survives reloads (`GET /pulse/last`)
- **Agent trace** — every proposal persists a hop-by-hop record of how the chain actually ran (source, model, measured duration, reflection/skeptic outcome, memory recalled) and shows it on the card
- **Agent bus + live feed** — every inter-agent hop (pickup, handoff, skeptic challenge and verdict, briefing, operator decision) publishes to a persisted feed; the dashboard's side panel streams the conversation live, and `GET /agents` names the six-agent team with roles, triggers and guardrails
- **Mission DSL** — declarative YAML missions (`configs/`) drive detection thresholds, detectors, escalation bars and the fraud rule bands; validated hard, with a reflex engine whose latency is measured, not claimed
- **Unified watch** — mock security events ride the identical detection line as cost (own mission, own event kind, never routed into the cost agents); payment events get a published deterministic rule score with per-rule point attribution — suggestions only
- **Guardrail pack** — per-pulse LLM call budget (overridable per run), hard transport timeout, ±5% numeric post-check of narrative figures, stakes-raised debate bar for bold answers to critical signals, prompt spotlighting for untrusted data
- **Operations intelligence** — HITL funnel, approved savings, window-over-window trend, month-end forecast with budget signal, what-if and before/after ROI, detection precision proxy, and a self-FinOps ledger of the system's own LLM spend
- Live dashboard: anomaly feed with a live sentinel radar, cost ledger, investigation evidence, decision inbox (with operator identity + rationale capture), audit ledger and operations intelligence — real page rooms (`/watch`, `/investigate`, `/decide`, `/intel`), four palettes, WCAG AA, strict CSP
- **Shift-handover brief** (`GET /analytics/handover`) — the standing operator questions answered from persisted state, printable to one page; a **guided jury tour** (`?tour=1`) walks the rooms in reading order
- **Fully self-contained** — every font is self-hosted (`static/fonts/`) and Swagger is vendored, so the CSP allows no remote host on any path; shareable deep links (`?threshold=&service=`) open on the exact scene, and a `[BOOT]` manifest names each instance on startup
- REST API (FastAPI, 32 endpoints) with self-hosted Swagger documentation (no CDN)
- Demo operations, all env-gated: whole-week date rebase, demo reset with seeded verdict history, read-only public showcase mode; a borderline signal makes the sensitivity slider meaningful (lower it, a third warning surfaces)

## What It Does / What It Deliberately Does Not

The whole contract on one table — the right column is design, not backlog:

| ✅ Does | 🚫 Deliberately does not |
|---|---|
| Detects cost & security anomalies over a rolling baseline (z-score / MAD, weekly seasonality, min-history discipline) | Connect to real cloud providers — synthetic data by design; the detection pipeline is source-agnostic |
| Reasons about every cost signal with AI agents: evidence-cited triage, two remediation options with risk + rollback, adversarial review of contested calls | Let a model invent numbers — every figure the operator acts on is deterministic Python arithmetic, post-checked ±5% against the narrative |
| Files proposals into a human decision inbox with rationale + actor capture and a full audit trail | Execute anything on real infrastructure — execution is simulated by design, and nothing runs unapproved |
| Scores payment events with published, hand-reproducible rules (per-rule point attribution) | Run ML fraud models, auto-block payments, or hide the scoring arithmetic |
| Remembers operator verdicts and feeds them back into future recommendations, disclosing how many were considered | Learn silently — memory use is visible on the card, and the chain's execution is traced hop by hop |
| Accounts for its own AI spend (call ledger, cache hits, fallbacks, quota view) under a per-run call budget | Burn quota unbounded, retry forever, or fail when the LLM is unavailable — every agent degrades to a labeled rule-based fallback |
| Ships hardened: strict CSP with self-hosted docs, security headers, rate-limited pulse, idempotent decisions, JSON failure envelope | Ship auth/RBAC, Postgres, schedulers or Slack — deliberate boundaries of this build, not oversights |

## Target Audience

- DevOps / platform engineering teams operating cloud infrastructure
- FinOps specialists managing cloud spending
- Security operations (SecOps) teams
- SMEs and startups that want to keep their cloud costs under control

## Three Roles, One Control Room

Companies run cloud operations through three roles, each with its own toolbelt
and its own daily question. CloudSentinel is designed as the surface where the
three meet **after** detection — the moment their current tools hand the
problem back to a human with nothing but a raw alert:

| Role | On their desk today | Their daily question | Where CloudSentinel answers it |
|---|---|---|---|
| **FinOps analyst** | AWS Cost Explorer, GCP billing alerts, spreadsheets | *"Why did spend jump, and what is it worth fixing?"* | Cost ledger with share-of-spend, trend curve with anomaly marks, deterministic Python-computed savings on every proposal, CSV export for the finance review |
| **DevOps / platform engineer** | Datadog / Grafana, PagerDuty, Terraform | *"What exactly do I change, and how do I roll it back?"* | Analyst triage with cited evidence rows, cautious / bold options each carrying risk **and a rollback plan**, execution that stays simulated until a human approves |
| **SecOps operator** | SIEM dashboards, IAM audit logs, ticket queues | *"Who decided what, and can I prove it?"* | Human-in-the-loop state machine with idempotent decisions, the append-only decision ledger, and security signals flowing through the same pipeline in Sprint 3 |

## What Makes CloudSentinel Different

Cloud providers and observability tools (AWS Cost Anomaly Detection, GCP cost
alerts, Datadog Cloud Cost Management) can already *detect* cost anomalies.
CloudSentinel's differentiator is what happens after detection: AI agents
reason about each anomaly, propose concrete remediation actions with risk
levels, and a human operator gives the final approval — closing the
detect → decide → act loop with human-in-the-loop safety instead of leaving
the operator alone with a raw alert. The agent design is documented — and now
implemented — in [docs/architecture.md](docs/architecture.md).

## The System at a Glance

Every piece of the product in one picture — data falls from the cloud, agents
reason about it, and nothing touches infrastructure without a human hand:

```mermaid
flowchart LR
    CLOUD[("☁️ cloud cost &amp; security data<br/>mock today · live adapters in Sprint 3")] --> DET

    subgraph deterministic core
        DET["Detector<br/>z-score per service"]
    end

    subgraph agent layer
        DET --> AN["Analyst<br/>triage + cited evidence"]
        AN --> SK["Skeptic<br/>debate-lite review"]
        SK --> REC["Recommender<br/>cautious / bold options"]
        MEM[("decision memory")] --> REC
    end

    subgraph human in the loop
        REC --> INBOX["decision inbox<br/>operator approves / rejects"]
        INBOX --> EXEC["simulated execution<br/>+ audit ledger"]
    end

    INBOX --> MEM
    EXEC --> DASH["live dashboard"]
```

The full design rationale, agent contracts and API evolution live in
[docs/architecture.md](docs/architecture.md).

## Repository Map

Short and flat on purpose — every path says what it holds:

```text
cloudsentinel/
├── main.py               ASGI entry point: routes, CSP/security headers, failure envelope
├── app/                  application package
│   ├── detection.py      detector registry — rolling window, z-score/MAD, seasonality
│   ├── missions.py       mission DSL — YAML configs, hard validation
│   ├── reflex.py         reflex engine — mission-resolved scans, measured latency
│   ├── analyst.py        Analyst agent — triage, evidence, reflection
│   ├── recommender.py    Recommender agent + debate-lite skeptic
│   ├── chronicler.py     Chronicler agent — pulse briefings
│   ├── security.py       security lane — same detection line, own event kind
│   ├── fraud.py          fraud lane — published deterministic rule score
│   ├── actions.py        human-in-the-loop action lifecycle
│   ├── decisions.py      decision memory retrieval + ledger export
│   ├── analytics.py      funnel, savings, trend/forecast/what-if/ROI, self-FinOps
│   ├── pulse.py          one-call end-to-end chain + persisted last run
│   ├── ops.py            env-gated demo reset
│   ├── llm.py            provider layer: Gemini, context-aware fake, fallbacks, budget
│   ├── db.py             SQLite core — WAL, idempotency, seed-on-startup
│   ├── models.py         Pydantic schemas
│   └── data/             mock datasets — cost, security events, payment events
├── configs/              mission YAMLs — finops, security, fraud
├── static/               dashboard — tokenized design system, 4 palettes, vendored Swagger UI
├── scripts/              smoke test, failure drill, detection benchmark, Gemini spike
├── tests/                387 pytest cases incl. performance budgets
├── docs/                 architecture & agent design
├── Makefile              setup / run / test / demo / smoke / drill
└── ProjectManagement/    sprint evidence packs (boards, screenshots)
```

## How to Run (Local)

Two commands to a running product:

```bash
make setup && make run        # or: make demo — fake provider, fresh dates, reset armed
make smoke                    # (other shell) 13-step PASS/FAIL sweep of the live chain
```

Or by hand:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn main:app --reload
```

Then open the dashboard at `http://127.0.0.1:8000/` (Swagger at `/docs`), or query directly:

```bash
curl "http://127.0.0.1:8000/anomalies"
# → detects the 2 planted spikes in the mock data:
#   compute 2026-06-29 (z=3.61) and database 2026-07-02 (z=3.60)
```

Per-service spending breakdown:

```bash
curl "http://127.0.0.1:8000/costs/summary"
# → total spend, per-service totals and each service's share of overall cost
```

Daily trend series (powers the dashboard's spend-trend chart and per-signal evidence sparkline):

```bash
curl "http://127.0.0.1:8000/costs/daily"
# → aligned per-service daily series, date axis and daily totals
```

Run the test suite with `SENTINEL_FAKE_LLM=1 .venv/bin/pytest` (the fake
provider keeps tests deterministic and quota-free).

The full agent chain can be driven end to end with one call — watch the
tagged `[SIGNAL]/[ANALYST]/[DEBATE]/[RECOMMENDER]/[HITL]` log stream in the
server output:

```bash
curl -X POST "http://127.0.0.1:8000/pulse"
# → detect → Analyst triage → (debate-lite) → Recommender → decision inbox
```

**Contributing setup (once per clone):** run `sh scripts/check_identity.sh` —
it verifies your git identity is GitHub-linked and installs the repo hooks
(Conventional Commits subject + trailer guard).

> On Windows, replace `.venv/bin/` with `.venv\Scripts\` in the commands above.

Or run it with Docker:

```bash
docker build -t cloudsentinel .
docker run -p 8000:8000 cloudsentinel
```

## Built With

| Technology | Purpose |
|---|---|
| **Python 3.12** | Core language (pinned in venv, CI and Docker) |
| **FastAPI + Uvicorn** | REST API and ASGI server |
| **Pydantic v2** | Typed request/response models and validation |
| **pytest + httpx** | Automated test suite (387 tests, incl. performance budgets) |
| **SQLite** (stdlib `sqlite3`) | WAL-mode persistence core: action lifecycle, decision memory, LLM cache, idempotency |
| **Docker** | Containerized, deployment-ready packaging |
| **Gemini** (`google-genai`) | LLM provider layer with quota-aware retry and rule-based fallback |
| **Miro** | Scrum board and product backlog (official bootcamp template) |

## Project Status — Sprint 1 Deliverables

| Deliverable | Description | Status |
|---|---|---|
| Mock cost dataset | 4 services × 14 days of synthetic costs with 2 planted spikes | ✅ [`data/mock_costs.json`](app/data/mock_costs.json) |
| Anomaly detection API | `GET /anomalies` — per-service z-score with typed responses | ✅ [`main.py`](main.py) · [`detection.py`](app/detection.py) |
| Cost summary API | `GET /costs/summary` — per-service spend aggregates and shares | ✅ [`main.py`](main.py) · [`detection.py`](app/detection.py) |
| Cyber dashboard | Root-served UI: anomaly feed, cost matrix, live threshold control | ✅ [`static/`](static/) |
| Test suite | 27 pytest cases: detection, aggregation, filtering, export, validation, dashboard | ✅ [`tests/`](tests/) |
| Containerization | `python:3.12-slim` image | ✅ [`Dockerfile`](Dockerfile) |
| Agent & HITL architecture design | Sprint 2–3 technical plan | ✅ [`docs/architecture.md`](docs/architecture.md) |
| Health check & CSV export | `GET /health` liveness · downloadable cost summary (PR #3) | ✅ [`main.py`](main.py) |

## Project Status — Sprint 2 Progress

Sprint 2's committed stories were code-complete by July 12 and the sprint closed 13/13 at the July 19 review; the second week additionally pulled the Sprint 3 core forward (second table):

| Deliverable | Description | Status |
|---|---|---|
| SQLite persistence core | WAL journal, write-lock discipline, idempotency keys, seed-on-startup for ephemeral disks | ✅ [`app/db.py`](app/db.py) |
| Analyst agent | `POST /anomalies/{id}/analyze` — triage badge + cited evidence + confidence, reflection on critical signals, response caching | ✅ [`app/analyst.py`](app/analyst.py) |
| Recommender + debate-lite | `POST /anomalies/{id}/recommend` — two options with Python-computed savings; skeptic review on low-confidence/contested calls | ✅ [`app/recommender.py`](app/recommender.py) |
| HITL action lifecycle | `GET /actions` · approve / reject / execute (simulated) with `Idempotency-Key` support and request-triggered timeouts | ✅ [`app/actions.py`](app/actions.py) |
| Decision memory | Operator verdicts stored and retrieved (`GET /decisions/similar`) and injected into the Recommender's context | ✅ [`app/decisions.py`](app/decisions.py) |
| Pulse end-to-end chain | `POST /pulse` — detect → analyze → debate → recommend → inbox with a tagged JSON log stream | ✅ [`app/pulse.py`](app/pulse.py) |
| Live dashboard | Sections I–V run against the real API: investigation triage, recommendation filing, decision inbox, audit ledger | ✅ [`static/`](static/) |
| Quota & safety discipline | Deterministic fake provider for tests/CI, rule-based fallbacks tagged in the UI, spotlighted untrusted data, security headers + CSP + CORS | ✅ [`app/llm.py`](app/llm.py) · [`main.py`](main.py) |
| Contributor tooling | Conventional-commit hook + identity check script | ✅ [`scripts/check_identity.sh`](scripts/check_identity.sh) |
| Dashboard interactivity | Persisted palette switch (horizon / **night** / paper / dawn), sortable signal ledger (z / date / a–z), click-to-filter cost rows, monotone-curve charts that never overshoot the data | ✅ [`static/`](static/) |
| Swagger CSP regression fix | `/docs` rendered blank under the strict dashboard CSP; a docs-scoped policy restored it, locked by regression tests | ✅ [`main.py`](main.py) · [`tests/test_dashboard.py`](tests/test_dashboard.py) |
| Performance budgets | Wall-clock budgets over scans, aggregations, CSV export and the full pulse chain on mock data | ✅ [`tests/test_performance.py`](tests/test_performance.py) |

**Sprint 3 core, pulled forward into Sprint 2's second week:**

| Deliverable | Description | Status |
|---|---|---|
| Detection quality | Calendar rolling baseline (28d), MAD detector, weekly seasonality, min-history discipline, `/metrics/detection`, benchmark harness | ✅ [`app/detection.py`](app/detection.py) |
| Mission DSL + reflex engine | Validated YAML missions drive thresholds, detectors, escalation and fraud bands; measured reflex latency; `/reflex/suggestions` | ✅ [`app/missions.py`](app/missions.py) · [`configs/`](configs/) |
| Unified security & fraud lanes | Security events through the identical detection line; payments scored by published rules with per-rule points; band filters | ✅ [`app/security.py`](app/security.py) · [`app/fraud.py`](app/fraud.py) |
| Cross-lane HITL cards | Fraud holds and the budget guard file deterministic cards into the same decision inbox | ✅ [`app/fraud.py`](app/fraud.py) · [`app/analytics.py`](app/analytics.py) |
| Guardrail pack | Per-pulse LLM call budget, hard timeout, ±5% numeric post-check, stakes-raised debate bar, prompt spotlighting | ✅ [`app/llm.py`](app/llm.py) |
| Operations intelligence | Funnel, savings, trend, forecast + budget signal, what-if, ROI, calibration, headline, self-FinOps AI ledger | ✅ [`app/analytics.py`](app/analytics.py) |
| Chronicler + agent trace | Sixth agent narrates each pulse; every proposal persists a hop-by-hop trace with measured durations | ✅ [`app/chronicler.py`](app/chronicler.py) |
| Agent bus + live feed | Inter-agent traffic persisted and streamed into the dashboard's live feed panel; agent roster endpoint | ✅ [`app/bus.py`](app/bus.py) |
| Ops & demo hardening | Self-hosted Swagger under one strict CSP, JSON failure envelope, read-only showcase, demo reset + date rebase, Makefile, smoke & failure-drill scripts | ✅ [`main.py`](main.py) · [`scripts/`](scripts/) |

## Roadmap (Sprint 2-3)

In line with [docs/architecture.md](docs/architecture.md) and the sprint point plan:

| Work | Sprint | Status |
|---|---|---|
| Gemini agents — Analyst (anomaly triage) & Recommender (action proposals) | Sprint 2 | ✅ shipped (running on the deterministic provider until the live key is provisioned) |
| Human-in-the-loop action lifecycle (`proposed → approved/rejected → executed`) | Sprint 2 | ✅ shipped |
| Decision memory feeding the Recommender | Sprint 2 | ✅ shipped |
| Security-signal ingestion through the same detection pipeline (mock events) | Sprint 3 → pulled forward | ✅ shipped |
| Fraud rule-score lane + cross-lane HITL cards (holds, budget guard) | Sprint 3 → pulled forward | ✅ shipped |
| Mission DSL, reflex engine, guardrail pack, operations analytics, chronicler, agent bus + live feed | Sprint 3 → pulled forward | ✅ shipped |
| Continuous integration — tests on every push | Sprint 2 → 3 | 🔄 blocked on token scope only |
| Dashboard palette revision after UI reference research | Sprint 2 → 3 | 🔄 switcher shipped (horizon / night / paper / dawn, persisted); final decision at the design session |
| Live Gemini key spike (real RPM/RPD measurement) | Sprint 3 | planned |
| User's-eye UX pass — gaps, friction and flow measured from the operator's seat | Sprint 3 | planned |
| Deployment (Render + UptimeRobot), live demo & 3-minute product video | Sprint 3 | planned |

## Requirements Compliance

Mapping of the official bootcamp scrum-notebook requirements to their evidence in this repository:

| Requirement | Status | Evidence |
|---|---|---|
| Team name & roles documented | ✅ | [Team Name](#team-name) · [Team Members](#team-members) |
| Product name, description, features, target audience | ✅ | [Information About the Product](#information-about-the-product) |
| Product Backlog board (Miro) | ✅ | [Product Backlog URL](#product-backlog-url) |
| Sprint Notes (never left empty) | ✅ | [Sprint 1](#sprint-1) · [Sprint 2](#sprint-2) |
| Point estimates & completion logic | ✅ | [Sprint 1](#sprint-1) · [Sprint 2](#sprint-2) |
| Daily Scrum documentation | ✅ | [Slack & WhatsApp evidence](ProjectManagement/Sprint1Documents/) |
| Sprint board screenshots | ✅ | [Miro board](ProjectManagement/Sprint1Documents/miro_board.jpeg) · [burndown](ProjectManagement/Sprint1Documents/burndown_sprint1.png) |
| Product status screenshots | ✅ | Sprint 2: [dashboard](ProjectManagement/Sprint2Documents/dashboard_cobalt.png) · [Swagger](ProjectManagement/Sprint2Documents/swagger_13_endpoints.png) — Sprint 1: [dashboard](ProjectManagement/Sprint1Documents/dashboard.png) · [Swagger](ProjectManagement/Sprint1Documents/swagger_docs.png) |
| Sprint Review & Retrospective | ✅ | [Sprint 1](#sprint-1) · [Sprint 2](#sprint-2) |
| Working product increment | ✅ | [`GET /anomalies`](main.py) · agent chain ([analyze](app/analyst.py) · [recommend](app/recommender.py) · [pulse](app/pulse.py)) · [HITL lifecycle](app/actions.py) · [tests](tests/) |

## Scope & Limitations (By Design)

These constraints are intentional Sprint 1 decisions, not oversights:

- **Synthetic mock data only** — real cloud-provider connectors are outside the
  competition scope; the detection pipeline is data-source agnostic by design.
- **Human-in-the-loop lifecycle landed in Sprint 2** — `GET /actions` plus
  `POST /actions/{id}/approve|reject` (idempotent via `Idempotency-Key`)
  implement the operator decision gate; nothing ever executes without an
  approval, and execution stays simulated
  (see [docs/architecture.md](docs/architecture.md)).
- **Security and fraud lanes landed with the Sprint 3 core** — mock security
  events ride the identical detection line (own mission and event kind), and
  payment events carry a published deterministic rule score; both are
  operator-facing suggestions, never agent conversations or automatic blocks.
- **Deployment lands in Sprint 3** — the app is containerized (non-root,
  healthchecked) with `render.yaml` ready; the live link follows the deploy.

## Product Backlog URL

[Miro Scrum Board — official bootcamp template](https://miro.com/app/board/uXjVH-p0md4=/?share_link_id=656166042252)

---

# Sprint 1

- **Sprint Notes**:
  - `FastAPI + Python` was chosen as the backend stack (required by the bootcamp guide).
  - `Gemini` is planned for the LLM layer.
  - `Miro` (the official bootcamp Scrum template) was chosen as the project management tool; `GitHub Projects` was not preferred due to data-loss experiences in previous terms.
  - It was decided that Daily Scrum meetings would be held over `WhatsApp`.
  - The scope of Sprint 1 was limited to a single anomaly-detection endpoint running on synthetic (mock) data; Gemini integration and the multi-agent architecture were deferred to later sprints.
  - Code, commit messages and all project documentation, including this scrum notebook, are kept in `English`.
  - Samet Kargın was unable to participate during Sprint 1; the team continues with four active members and the Sprint 1 stories were distributed accordingly.

- **Expected point completion within the sprint**: 10 points

- **Point Completion Logic**: The total backlog planned for the whole project is 36 points. Since Sprint 1 was shortened due to the late formation of teams, the target for this sprint was set at 10 points. The remaining points are split between Sprint 2 (13 points) and Sprint 3 (13 points).

- **Backlog order and story selections**: The backlog is ordered by the stories that will be tackled first. The estimate for each story is kept below half of the sprint total. Sprint 1 stories: repository skeleton and mock cost data (3 points), anomaly detection logic (4 points), `GET /anomalies` endpoint with Swagger documentation (3 points). Stories are split into tasks on the Miro board and assigned across the four team members. On the board, blue cards represent user stories and red/orange cards represent tasks (see the legend on the board itself).

- **Daily Scrum**: Daily communication runs over WhatsApp; team meetings and huddles are held on Slack. Evidence in [ProjectManagement/Sprint1Documents/](ProjectManagement/Sprint1Documents/): [team formation & GitHub sharing](ProjectManagement/Sprint1Documents/slack_team_github_sharing.jpeg) · [project pitch](ProjectManagement/Sprint1Documents/slack_project_pitch.jpeg) · [meeting scheduling & 2h huddle](ProjectManagement/Sprint1Documents/slack_meeting_and_huddle.jpeg) · [in-team design review request](ProjectManagement/Sprint1Documents/whatsapp_design_review_request.jpeg) · [design feedback & decision](ProjectManagement/Sprint1Documents/whatsapp_design_feedback.jpeg).

- **Sprint board update**:

  ![Miro Scrum Board](ProjectManagement/Sprint1Documents/miro_board.jpeg)

  ![Sprint 1 Burndown](ProjectManagement/Sprint1Documents/burndown_sprint1.png)

  Detail: [Done column with per-member assignments](ProjectManagement/Sprint1Documents/miro_board_done_column.jpeg).

- **Product Status**: the increment runs locally — dashboard at `/`, Swagger at `/docs`.

  ![CloudSentinel dashboard](ProjectManagement/Sprint1Documents/dashboard.png)

  ![Swagger UI — four endpoints](ProjectManagement/Sprint1Documents/swagger_docs.png)

  More: [cost ledger & footer](ProjectManagement/Sprint1Documents/dashboard_ledger.png) · [typed schemas](ProjectManagement/Sprint1Documents/swagger_schemas.png).

- **Sprint Review**: Sprint 1 closed with all three committed stories completed (10/10 points). Beyond the committed scope, three teammate pull requests were reviewed and merged during the sprint — per-service cost summary (PR #1), case-insensitive service filter for `/anomalies` (PR #2), and `/health` plus CSV export (PR #3) — and the dashboard was pulled forward from Sprint 3 as a bonus, so every team member shipped reviewed, merged code in Sprint 1. The increment was demoed over the dashboard and Swagger and behaves correctly: 27 automated tests, both planted anomalies detected with zero false positives. Decisions taken: security-signal ingestion stays in scope and will flow through the same detection pipeline in Sprint 3 with mock security events, as designed in [docs/architecture.md](docs/architecture.md); the 36-point plan (10/13/13) was confirmed; the dashboard's cobalt palette will be revisited in Sprint 2 after UI reference research. Carried over to Sprint 2: Gemini integration (Analyst + Recommender agents), the human-in-the-loop action lifecycle, the decision-memory store, and a code packaging refactor.

  | Story | Points | Result |
  |---|---|---|
  | Repository skeleton & mock cost data | 3 | ✅ Completed |
  | Anomaly detection logic (z-score) | 4 | ✅ Completed |
  | `GET /anomalies` endpoint + Swagger documentation | 3 | ✅ Completed |
  | Bonus: cost summary (PR #1) · service filter (PR #2) · `/health` & CSV export (PR #3) · dashboard | — | ✅ Delivered |
  | **Total** | **10 / 10** | |

- **Sprint Review Participants**: `Tuana Aydın, Muratcan Ateş, Çağla Yurtseven, Mert Kurt`

- **Sprint Retrospective**:
  - **What went well**: a working increment was ready two days before the sprint deadline; scope discipline held with no feature creep; the team switched to a pull-request workflow mid-sprint and all three teammate PRs were reviewed and merged the day they were opened; the scrum notebook, architecture design and evidence pack were kept current throughout the sprint.
  - **What to improve**: the late team formation compressed delivery into the final days of the sprint (clearly visible in the burndown chart); the project-management board was set up late; in-team design review surfaced that the dashboard's cobalt background is tiring on the eyes.
  - **Action items**: the Sprint 2 board is filled before planning on July 6; evidence (board and daily screenshots) is captured weekly rather than at sprint end; the Gemini API spike is the first task of Sprint 2; the dashboard palette is revised after UI reference research (owner: Tuana); every member ships at least one reviewed PR per sprint.

---

# Sprint 2

*Sprint 2 ran July 6 – July 19; the sprint review and demo close it on July 19.*

- **Sprint Notes**:
  - Sprint 2's goal is the agent layer on top of the Sprint 1 detection core: Analyst and Recommender agents, the human-in-the-loop action lifecycle, and decision memory — as designed in [docs/architecture.md](docs/architecture.md).
  - The LLM layer was built provider-agnostic: a deterministic fake provider (`SENTINEL_FAKE_LLM=1`) drives all tests and offline demos, and the rule-based fallback path keeps every endpoint answering even with the LLM unavailable. The live Gemini key is provisioned from a billing-disabled project, so the quota-safety posture stays zero-cost by construction.
  - Quota discipline was locked early: responses are cached, reflection runs only on critical signals, and the debate-lite skeptic costs at most one extra call per decision.
  - The Recommender's prompt interface was frozen mid-sprint so decision memory could be injected later as a single isolated change — which is exactly how it landed.
  - Money figures shown to the operator are computed deterministically in Python; the model narrates, it never invents numbers.
  - Execution stays simulated by design: the state machine records an executed action with a SIMULATION marker and no real infrastructure is ever touched.
  - The dashboard was rebuilt on a tokenized design system with three palette directions (cobalt / mission / paper); a persisted in-dashboard switch — night mode included — now lets the team and reviewers flip palettes live. The final palette decision (retro action item, owner: Tuana) lands at the Friday design session.
  - Mid-sprint hardening from the July 12 review requests: interactive ledger tables (sortable signals, click-to-filter cost rows), monotone-curve charts, performance budget tests over the mock-data pipeline, and a regression fix that restored Swagger UI under the strict CSP.
  - The sprint's second week pulled the **Sprint 3 core forward**: detection quality (rolling baseline, MAD, weekly seasonality), the mission DSL + reflex engine, the unified security and fraud lanes, the guardrail pack (call budget, timeout, numeric post-check), the operations-intelligence analytics, the chronicler agent, the persisted agent trace and finally the **agent bus with a live feed panel** — the whole inter-agent conversation streaming into the dashboard as it happens.
  - The fraud lane is developed in this repository as published deterministic rule arithmetic (no ML); its strongest signals and a projected budget overrun now file cards into the same human decision inbox — three missions, one decision box.
  - The CI restore remains the open non-code item of the sprint (token scope pending).

- **Expected point completion within the sprint**: 13 points

- **Point Completion Logic**: Sprint 2 carries 13 of the 36 total backlog points: Gemini agent spike (2), Analyst agent (3), Recommender with debate-lite (3), human-in-the-loop lifecycle (3), decision memory (2). All five stories are code-complete as of July 12 — the suite has since grown to 387 automated tests (~7s on the fake provider) — and formal completion is assessed at the July 19 review and demo.

- **Daily Scrum**: daily communication continues over WhatsApp with team meetings on Slack; evidence screenshots are collected in `ProjectManagement/Sprint2Documents/` through the sprint.

- **Product Status — input → output on the running increment**: full-page captures of the Sprint 2 dashboard on mock data. One `POST /pulse` (input) drives the whole chain; the inbox, ledger and summary strip below are the output of exactly that call plus one operator approval:

  ```bash
  curl -X POST http://127.0.0.1:8000/pulse
  ```

  ```json
  {"threshold": 2.0, "signals": 2, "analyzed": 2, "proposals_filed": 2,
   "chain": [
     {"event_id": 1, "service": "compute",  "severity": "critical", "triage": "REAL",
      "action_id": 1, "action_state": "proposed", "preferred": "CAUTIOUS"},
     {"event_id": 2, "service": "database", "severity": "critical", "triage": "REAL",
      "action_id": 2, "action_state": "proposed", "preferred": "CAUTIOUS"}]}
  ```

  ![CloudSentinel Sprint 2 dashboard — cobalt](ProjectManagement/Sprint2Documents/dashboard_cobalt.png)

  Both planted spikes are detected, triaged REAL, filed as proposals; the compute action was approved and executed — SIMULATION, the database action still awaits the hand. Palette directions for the design decision: [night](ProjectManagement/Sprint2Documents/dashboard_night.png) · [paper](ProjectManagement/Sprint2Documents/dashboard_paper.png) · [Swagger — 13 endpoints](ProjectManagement/Sprint2Documents/swagger_13_endpoints.png).

- **Sprint Review**: Sprint 2 closed with all five committed stories completed (13/13 points), demoed end to end at the July 19 review over `POST /pulse`: detect → Analyst triage with cited evidence → debate-lite skeptic → Recommender options with Python-computed savings → decision inbox → operator verdict → decision memory. Everything ran on the deterministic provider (the live Gemini key is provisioned separately from a billing-disabled project), which is itself the demo's point: the agent layer degrades honestly and never blocks on quota. Beyond the committed scope, the second week of the sprint pulled the Sprint 3 core forward — detection-quality controls, the mission DSL and reflex engine, the unified security and fraud watch, the guardrail pack, the operations-intelligence analytics, the chronicler agent, the persisted agent trace, the live agent-feed panel, cross-lane HITL cards (fraud holds and the budget guard) and self-hosted Swagger under one strict CSP — growing the suite from 27 tests at Sprint 1 close to **378 tests over 32 endpoints**. Decisions taken: the fraud lane stays rule-based and in-repo; deployment (Render, `render.yaml` ready) and the live-key spike open Sprint 3; the final palette decision is carried into the Sprint 3 design session with the three-way switcher shipped.

  | Story | Points | Result |
  |---|---|---|
  | Gemini agent spike — provider layer, retry, fallback, fake provider | 2 | ✅ Completed |
  | Analyst agent — triage, cited evidence, reflection at critical z | 3 | ✅ Completed |
  | Recommender + debate-lite skeptic — two options, computed savings | 3 | ✅ Completed |
  | Human-in-the-loop lifecycle — approve / reject / simulated execute | 3 | ✅ Completed |
  | Decision memory — verdicts stored, retrieved and fed to the agent | 2 | ✅ Completed |
  | Bonus: Sprint 3 core pulled forward (detection quality · missions · lanes · guardrails · analytics · chronicler · agent bus) | — | ✅ Delivered |
  | **Total** | **13 / 13** | |

- **Sprint Review Participants**: `Tuana Aydın, Muratcan Ateş, Çağla Yurtseven, Mert Kurt`

- **Sprint Retrospective**:
  - **What went well**: the fake-provider discipline let the entire agent layer land and demo with zero LLM spend; freezing the Recommender's prompt interface early meant decision memory landed later as a single isolated change, exactly as planned; the July 12 mid-sprint review requests were absorbed without breaking stride; pulling the Sprint 3 core forward leaves the final sprint free for deployment, the video and polish; the suite grew 27 → 378 tests with ruff clean on every commit.
  - **What to improve**: the second-week push concentrated commits on one member — the pull-request rhythm from Sprint 1 (every member ships reviewed PRs) slipped and must return; CI is still blocked on a token scope, so green runs live only on developer machines; the live Gemini key was not provisioned during the sprint, so real-quota behavior (RPM/RPD) remains unmeasured.
  - **Action items**: Sprint 3 work is distributed as reviewed PRs across all four members from the board; the Gemini key spike (`scripts/spike_gemini.py`) is the first task of Sprint 3; the CI workflow lands the day the token scope does; Render deployment closes before the July 25 gate with UptimeRobot on `/health`; the final palette decision is taken at the Sprint 3 design session (owner: Tuana).

---

# Sprint 3

*The final sprint runs July 20 – August 2 and closes with deployment, the live demo and the 3-minute product video.*

- **Head start**: the Sprint 3 technical core was pulled forward into Sprint 2's second week and is already shipped — detection quality (rolling baseline / MAD / weekly seasonality), the mission DSL + reflex engine, the **security lane through the identical detection line**, the fraud rule-score lane with cross-lane HITL cards, the guardrail pack, operations-intelligence analytics, the chronicler agent, the agent bus with its live feed panel, self-hosted Swagger and the demo-operations knobs (date rebase, demo reset, read-only showcase).

- **Remaining scope**:
  - **Live Gemini spike** — provision the billing-disabled key and measure real RPM/RPD with `scripts/spike_gemini.py`; the whole chain already runs on the deterministic provider, so this lights up narratives, not correctness.
  - **Continuous integration** — the workflow lands the day the token scope does; the suite and ruff are already green on every commit.
  - **Deployment** — Render (`render.yaml` ready, non-root healthchecked image) with UptimeRobot on `/health`; the dashboard's LIVE banner switches on via `SENTINEL_ENV=render`.
  - **User's-eye UX pass & final palette** — friction measured from the operator's seat; the palette decision at the design session (three-way switcher shipped).
  - **Evidence & submission** — sprint documents, the 3-minute product video, and the August 2 form.

---

# Field Guide — Sixty Seconds to a Decision

1. **Run it** — `uvicorn main:app --reload`, open `http://127.0.0.1:8000/` (Swagger at `/docs`).
2. **Tune the watch** — drag the sensitivity slider or pick a service; section I re-scans live. Prefer the dark room? Flip the palette to **night** in the control rail — the choice persists.
3. **Investigate** — hit *investigate →* on a signal: evidence sparkline, baseline, deviation, then *run analyst agent →* for triage with cited rows.
4. **Watch them talk** — open the **agent feed** rail (bottom right): every hop of the chain — pickups, handoffs, skeptic challenges, verdicts, briefings — streams in live as it happens.
5. **Decide** — *file recommendation →*, type a rationale, then approve or reject in the inbox. Execution is always a simulation, and the ledger remembers every hand that touched it.

# In Short

CloudSentinel closes the gap between *"your cloud bill spiked"* and *"someone accountable did something about it"*: a deterministic detector finds the spike, AI agents explain it and propose two ways out with computed savings, a skeptic challenges weak calls — and nothing executes until a human says so, in writing, forever.

# Acknowledgements

- **Yapay Zeka ve Teknoloji Akademisi** — for the YZTA Bootcamp 2026 program, the scrum template and the mentoring hours behind this repo.
- **Team CloudSentinel** — every feature here crossed at least one teammate's review before it landed.
- **The open tools that carried us** — FastAPI, Pydantic, pytest, SQLite, Docker, Gemini (`google-genai`), and the Google Fonts faces (Instrument Serif, Jacquard 24, UnifrakturMaguntia) that give the dashboard its voice.
- **Michelangelo** — for the two hands we borrowed; the machine watches, the human decides.

---

<img src="docs/img/banner_hands.png" alt="every action awaits a human hand" width="100%" />

<div align="center"><sub>Built by Team CloudSentinel — YZTA Bootcamp 2026 · AI Track · Group 60</sub></div>
