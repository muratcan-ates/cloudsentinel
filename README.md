<img src="docs/img/banner.png" alt="CloudSentinel — the machine watches, the human decides" width="100%" />

<div align="center">

# ☁️ CloudSentinel

### AI-agent powered cloud cost & security anomaly detection — with a human in the loop

**YZTA Bootcamp 2026 · AI Track · Group 60**

**🟢 [Live demo](https://cloudsentinel-y5zh.onrender.com)** — read-only showcase, self-refreshing · [Product](#information-about-the-product) · [Architecture](docs/architecture.md) · [How to Run](#how-to-run-local) · [Sprint 2](#sprint-2) · [Sprint 3 Backlog](docs/sprint3_backlog.md) · [Field Guide](#field-guide--sixty-seconds-to-a-decision) · [Türkçe Özet](docs/README.tr.md)

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.139-009688?logo=fastapi&logoColor=white)
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
- [Sprint 1](#sprint-1) · [Sprint 2](#sprint-2) · [Sprint 3](#sprint-3) · [Sprint 3 Backlog](docs/sprint3_backlog.md) · [48-Hour Closeout](docs/CLOSEOUT_48H.md)
- [Field Guide](#field-guide--sixty-seconds-to-a-decision) · [In Short](#in-short) · [Acknowledgements](#acknowledgements) · [Türkçe Özet](docs/README.tr.md)

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
- **Recommender agent** — proposes exactly two options (cautious / bold) with risk and rollback plans; savings figures are **scenario estimates** (30-day horizon, cautious/bold capture rates, assumes the excess persists) computed deterministically in Python, never by the model
- **Debate-lite skeptic** — low-confidence or contested recommendations get one extra adversarial review; the transcript ships with the proposal
- **Decision memory** — operator verdicts are stored and fed back into the Recommender's context, so repeated anomaly patterns meet an agent that remembers
- **Human-in-the-loop lifecycle** — `proposed → approved/rejected → executed (simulated)` with idempotent decisions, request-triggered timeouts and a full audit trail; nothing ever executes without a human. Execution of the infrastructure change is simulated by design — and when an operator configures a webhook (`SENTINEL_EXECUTE_WEBHOOK_URL`), the decided incident is **really dispatched** to their endpoint and the delivery outcome lands in the audit detail
- **Tamper-evident ledger** (`GET /audit/verify`) — "append-only" was an architectural claim, and an unfalsifiable one: anyone with the database file could open it in `sqlite3` and rewrite a verdict. Every decision and lifecycle transition is now sealed at write time, inside the caller's transaction, with the hash of the entry before it (SHA-256 over `prev_hash | stream | ref_id | canonical row body`). The endpoint recomputes the chain from genesis against the live source rows rather than asserting it intact, and reports the **first** broken link and which of four ways it broke — an entry spliced in or dropped, the ledger row itself edited, the source row rewritten, the source row gone. Rows that never came through the decision desk (the `?seed=1` demo verdicts) are reported as `unsealed` instead of being quietly absorbed. The chain proves the history was not rewritten; it does not make the history survive a restart, which is still Postgres's job
- **Alert suppression** — per-event dedupe already stopped the same signal minting a second card; it did nothing about the operator's real burden, a service that deviates again tomorrow and again the day after while the first card is still unanswered. Now, while an **undecided** card speaks for a service on a lane, later signals fold into it as counted repeats carrying their dates and z-scores, so the inbox stays one card and reads "this is the third day" at a glance. Nothing is discarded, the window is configurable (`SENTINEL_SUPPRESSION_WINDOW_HOURS`, 24 h default, `≤ 0` disables), and the fold is scoped by event kind so a cost card can never silence a fraud hold on the same service. The moment a human approves, rejects or executes, that conversation is closed and the next signal earns its own card — folding into a decided card would apply an old verdict to a new fact
- **Pulse + Chronicler** — one call drives the whole chain (detect → analyze → debate → recommend → inbox) with a tagged JSON log stream; a chronicler agent narrates every run into an operator briefing, and the last run survives reloads (`GET /pulse/last`)
- **Agent trace** — every proposal persists a hop-by-hop record of how the chain actually ran (source, model, measured duration, reflection/skeptic outcome, memory recalled) and shows it on the card
- **Agent bus + live feed** — every inter-agent hop (pickup, handoff, skeptic challenge and verdict, briefing, operator decision) publishes to a persisted feed; the dashboard's side panel streams the conversation live, and `GET /agents` names the six-agent team with roles, triggers and guardrails
- **Mission DSL** — declarative YAML missions (`configs/`) drive detection thresholds, detectors, escalation bars and the fraud rule bands; validated hard, with a reflex engine whose latency is measured, not claimed. The three missions declare genuinely different postures, so flipping the switch changes the numbers rather than the label: **security** watches at 1.75 over a 14-day window and scores with **MAD** (a credential burst is small and fast, it must be caught before it is large, and it inflates the very mean a z-score would measure it against); **fraud** rises to 2.75 over 21 days because the published rule score is that lane's primary instrument and a low statistical bar would drown it in noise it never raised; **finops** is deliberately untouched at 2.0 over 28 days, since it is the lane the demo walks through and every screenshot, test and figure already pins its numbers. The debate bars move with them — 0.75 security, 0.6 finops, 0.5 fraud
- **Reflex-rule drafts** (`GET /reflex/suggestions`) — decision memory is mined for signatures (service · severity · direction · category) the operators have approved unanimously inside the window. Each draft states its condition, the most conservative threshold that still covers every approval (a threshold at the mean would propose a rule for cases the humans have not actually seen), the decisions and actions it rests on, the median deliberation hours and the sentence explaining itself. Contested signatures — any rejection, or two different stances — are counted and excluded rather than averaged away. There is no adoption code path anywhere in the repository: the machine drafts the reflex, the human enacts it, and that asymmetry is enforced by absence rather than by a flag someone could flip
- **Unified watch** — mock security events ride the identical detection line as cost (own mission, own event kind, scored deterministically with no LLM agent, never routed into the cost agents); an **experimental fraud lane** runs the same governance rails on a third source — payment events get a published deterministic rule score with per-rule point attribution — suggestions only, a demonstration that the human-in-the-loop infrastructure generalizes, not a production fraud engine
- **Debate ladder** — a contested warning signal gets one adversarial Skeptic review; a contested **critical** signal convenes a three-seat heterogeneous review panel (three Gemini variants when live on one billing-disabled key, three deterministic personas offline) whose majority decides the stance with dissent and abstentions on the record; a service tripping the reflex on three anomaly days inside two weeks forces the debate even at high confidence
- **Mission quick-switch** — a dashboard dropdown flips the active mission live through `POST /pulse?mission=` (in-memory override); thresholds, detector and the debate bar re-read from another YAML and every mission-following surface flips together — one engine, three missions, proven on stage
- **Guardrail pack** — per-pulse LLM call budget (overridable per run), hard transport timeout, ±5% numeric post-check of narrative figures, stakes-raised debate bar for bold answers to critical signals, prompt spotlighting for untrusted data; the pipeline's contract is measured by a 200-case golden-set eval ([scorecard](docs/EVAL_SCORECARD.md))
- **Named uncertainty, per agent turn** — every hop publishes what is shaky about *that* answer, derived from the evidence it was handed rather than self-reported: a baseline shorter than the evidence window, a service with no history to compare against, a narrative citing no frozen row, a flagged day sitting inside the baseline it is measured against, seasonality off where a regular weekly peak would read as a surprise, a warning-grade signal, a panel seat that abstained, a panel short of the quorum needed to overrule a draft at all, low upstream confidence, no operator precedent, and a simulated provider. The list is identical whether Gemini, the demo composer or the rule-based fallback wrote the prose — a confidence score can be talked up, these cannot, and `/analytics/quality` tallies which of them fire most often
- **Operations intelligence** — HITL funnel, approved savings, window-over-window trend, month-end forecast with budget signal, what-if and before/after ROI, detection precision proxy, and a self-accounting ledger of the system's own AI usage — calls, cache hits, fallbacks and free-tier quota, zero-cost by design
- **Decision quality** (`GET /analytics/quality`) — the measures that move when the product gets better at *deciding* rather than better at generating: acceptance rate, mean and median time-to-decision read off the append-only trail (so timeout expiries and reopened cards cannot flatter it), per-service acceptance and recurrence, what one human decision cost in model calls, the average agent confidence across every hop that spoke, which named uncertainty sources fire most often, and the confidence-calibration buckets. Plain SQL over persisted state — no model is called and no figure is estimated
- **Run receipts** (`GET /analytics/receipts`) — the agentic equivalent of an itemised bill, one per watch cycle: signals, proposals filed and reused, agent turns, panel seats answered, turns that went unmeasured, the reflex and agent milliseconds actually measured, wall clock, the per-run LLM call budget against the calls used, and money once a price per call is configured. Assembled entirely on the read side from records the pulse already leaves behind, so asking for the receipt never changes what the run cost
- Live dashboard: anomaly feed with a live sentinel radar, cost ledger, investigation evidence, decision inbox (with operator identity + rationale capture), audit ledger and operations intelligence — real page rooms (`/watch`, `/investigate`, `/decide`, `/intel`, `/brain`, `/broadsheet`), five palettes, WCAG AA, strict CSP
- **The desk** — a card surface that reads the estate in three columns: what the system is holding (open signals, awaiting you, decided), what it can *prove* about itself, and what is waiting on a human. Every capability row is a live fetch of the endpoint it names, and a row that cannot answer says `unavailable` rather than showing a hopeful dash — a dash reads as zero, and zero is a claim. It exists because a broadsheet gives every row the same weight, which is exactly what makes it beautiful and exactly what hid the endpoints that landed last
- **A fifth palette and an accessibility panel** — `vivid` joins the four editorial palettes (horizon · night · paper · dawn): light ground, white cards with a real shadow, one saturated blue for anything actionable, colour used as a lane signal rather than decoration, and a wider measure because a control surface is not prose. The four editorial palettes are untouched, so one click restores the newspaper mid-demo. The accessibility panel sets text scale, line height, letter spacing, a readable face, highlighted links and headings, a reading mask, a larger pointer and forced contrast — every switch is a data attribute on `:root` persisted in the browser, no inline styles (the CSP forbids them), no third-party overlay and nothing loaded from another host
- **Shift-handover brief** (`GET /analytics/handover`) — the standing operator questions answered from persisted state, printable to one page; a **guided jury tour** (`?tour=1`) walks the rooms in reading order
- **Fully self-contained** — every font is self-hosted (`static/fonts/`) and Swagger is vendored, so the CSP allows no remote host on any path; shareable deep links (`?threshold=&service=`) open on the exact scene, and a `[BOOT]` manifest names each instance on startup
- **A production profile that refuses a demo posture** — every safety property here is an environment variable that defaults to off, which is right for a laptop and silent everywhere else. Under `SENTINEL_ENV=production` each gap is fatal and the app refuses to boot, naming the fix: writes open to anyone, an approver requirement with no bootstrap admin (or one whose password is under twelve characters), the deterministic fake provider about to answer real users, the outbound guard's developer escape hatch left open. Every other profile — this deployment's `render` showcase included — logs the identical findings as `[CONFIG]` lines and behaves exactly as before. The check exists before the deployment that needs it, which is the only order that ever works
- **Outbound targets are checked before the socket opens** — the feed and webhook URLs are configuration rather than user input today, but an unguarded fetch is a server-side request forgery waiting for the day they are not: point a "feed" at `169.254.169.254` and the app reads the cloud instance metadata for you, from inside the trust boundary. The guard allows https only and refuses loopback, link-local, private, carrier-NAT, multicast, reserved and unspecified destinations — literal *and* resolved — while the callers never follow redirects, because a public host answering `302 → 169.254.169.254` would walk straight through an address check that already passed. A name that does not resolve is allowed through, since the request behind it cannot reach anything either. `SENTINEL_ALLOW_PRIVATE_TARGETS=1` reopens http for a developer pointing at a local stub, and the boot audit names that as a gap
- **Correlation ids and machine-readable logs** — the HTTP layer mints (or accepts) an `X-Request-ID`, binds it to a context variable that follows both async and threadpool work, and returns it on the response, so every `[SIGNAL]/[ANALYST]/[DEBATE]/[RECOMMENDER]/[HITL]` line the chain emits carries the request it belongs to; two operators clicking at once no longer interleave into one unreadable stream. `SENTINEL_LOG_FORMAT=json` re-emits the whole stream as one object per line — level, timestamp, logger, the bracketed tag re-expanded into real fields rather than left as a string for the pipeline to parse a second time, and lines it does not recognise passed through with their message intact. Opt-in on purpose: with the knob unset the output is byte-identical to the lines the demo reads out loud
- **Live tape, simulated** — `SENTINEL_SIM_STREAM=1` (on in `make demo`) adds a trading-floor ticker to the watch room: per-service run-rates on a mean-reverting random walk with occasional spikes, sparklines refreshing every 2.5 s. Synthetic by construction and labeled as such on the strip and in the payload (`simulated: true`) — no billing data, no credentials, and the strip never appears on deployments without the flag. With `SENTINEL_COSTS_SOURCE=sim` (`make demo-sim`) the same stream also **drives the cost lane end to end**: the demo estate's own history — planted spikes and all — brought up to today, with TODAY projected live from the run-rate onto each service's own historical spread. A calm day stays quiet and a genuine excursion is flagged at a credible z-score (measured: the calm lane crosses the threshold under 5% of the time, a doubling reads z ~ 2.8), the trend chart carries a breathing marker on today's point, and the badge reads `SIMULATED LIVE` — never plain "live data"
- REST API (FastAPI, 59 endpoints) with self-hosted Swagger documentation (no CDN); a `/health` liveness ping and a `/ready` readiness probe (database, mission config and dataset) for deploy/uptime gating
- **`GET /metrics`** — a Prometheus text exposition of what this instance already counts: build info, decision cards by lifecycle state, verdicts, model calls by source and cache hit, requests served through its own telemetry, and the standing watch's condition. Written by hand in about forty lines rather than added as a dependency, read from the tables that hold the numbers rather than a registry kept warm between scrapes (so a scraper that never arrives costs nothing), and a source that cannot be read is **omitted rather than reported as zero** — on a graph those mean different things and only one of them would be true. No per-request histograms: a scrape endpoint that keeps its own state is a memory leak waiting for a slow scraper, and the run receipts already carry measured durations
- **The watch reports on itself** — `/health` answers as long as the *process* is up, which is exactly how a sentinel that froze at 20:40 kept returning 200 for three hours before anyone noticed. `GET /ops/health/watch` publishes the watch's own vitals — last successful beat, staleness against its configured cadence, consecutive failed ticks — and `/ready` folds them in as `degraded`: 200, deliberately not 503, because a slipped heartbeat must not take the public showcase down with it. `GET /ops/preflight` is [docs/DEMO_PREFLIGHT.md](docs/DEMO_PREFLIGHT.md) as code — dataset, mission, provider, read-only posture, watchdog, last pulse age, writable disk, data sources, security headers and demo reset, each pass / warn / fail with a sentence and one `ok` at the top, so the checks a human runs by eye before a take are one call instead of a checklist
- **Live data modes, env-gated** — the bundled datasets are the default (hermetic tests, reproducible demo), and each lane can go live: `SENTINEL_COSTS_SOURCE=self` runs the cost lane over the app's **own request telemetry** (`GET /telemetry/usage` — real traffic, accumulating while the server runs, `make demo-live`); `SENTINEL_COSTS_FILE` serves a **real billing export** converted credential-free by `scripts/import_costs.py` (Azure Cost Management / AWS CUR CSV headers recognized); and `SENTINEL_COSTS_FEED_URL` / `SENTINEL_SECURITY_FEED_URL` / `SENTINEL_FRAUD_FEED_URL` poll external JSON feeds in the exact mock contract (TTL-cached, malformed records dropped, failures fall back feed → last good payload → fixture); `/health` names each lane's source **as served, not as configured** and the dashboard's data badge renders it honestly — the statistical organs still demand real accumulated history before they score a live lane (no fabricated days)
- Demo operations, all env-gated: whole-week date rebase, demo reset with seeded verdict history, read-only public showcase mode; a borderline signal makes the sensitivity slider meaningful (lower it, a third warning surfaces)
- **Decision brain** (`GET /insights`) — reflects on persisted history into observations, a run-rate cost projection and improvement recommendations, all computed not generated; a **self-review cycle** (`POST /insights/self-review`) proposes changes to the system itself (reflex candidates, threshold reviews, calibration, backlog) and applies nothing, publishing the cycle to the agent feed
- **Market watch** (`GET /market/opportunities`) — the anomaly lanes answer *what changed*; this one answers *what is worth doing anyway*. A curated catalogue of published market moves (commitment discounts, ARM families, non-prod scheduling, storage tiering, spot capacity, idle sweeps, egress routing) is matched to the services the estate actually runs and costed against each service's own run rate: `run rate × addressable share × published band`. Every row ships its source, the date the team last checked it and the assumption it rests on; the gross total is labelled an upper bound because bands over one service overlap. Suggestions only — this lane never files an action. `SENTINEL_MARKET_FEED_URL` swaps the bundled catalogue for an external one on the same feed discipline (TTL cache, malformed rows dropped, fall back to curated)
- **Routines** (`/routines`, `/routines/suggestions`) — saved, read-only analysis playbooks plus a routines agent that suggests them from the current state, runnable on demand
- **Local identity** (`/auth`) — register/login with salted PBKDF2 and `viewer/analyst/approver/admin` roles; a signed-in operator's identity is **server-derived** onto every approval and rejection, so the audit trail is not free browser text
- **Signal enrichment** — each incident report carries a blast-radius tier (L0 contained → L3 severe, from the deviation magnitude), an industry-framework reference, a post-action verification plan and a cited remediation runbook from a curated, keyword-matched library (`/runbooks`, RAG-lite, offline). The framework reference is a deep-linked **MITRE ATT&CK** technique for the security and fraud lanes — keyed to the surface, so `auth-gateway` reads T1110 Brute Force, `api-edge` T1110.003 Password Spraying, `admin-portal` T1078.004 Valid Accounts, and a fraud hold T1657 Financial Theft — and a **FinOps Framework** capability for cost, keyed to the card's category (rightsizing → Workload Optimization, lifecycle → Architecting for Cloud, budget guard → Budgeting, and so on). The mapping is a table, not a model call: the answer is the same every time because it was looked up, and a surface we cannot map honestly falls back to the lane's general entry instead of inventing a plausible id
- **Runbook effectiveness** (`GET /runbooks/effectiveness`) — the library's five runbooks carry their own hit rate over the verdicts on record. A runbook whose matched cards were approved at 70% or above is promoted one rank step, 30% or below demotes it one step, and under three decided cards nothing moves at all. Recomputed on read and never stored — matching is deterministic, so the association between a decided card and a runbook is a function rather than a record that could drift — with the adjustment capped at a single step and no model anywhere in it
- **Detection backtest** (`GET /metrics/backtest`) — precision/recall on planted synthetic ground truth across z-score, MAD and leave-one-out scoring, so contamination resistance is measured, not claimed. The day-of-week baseline now runs too: the bundled fourteen-day fixture can never satisfy the guard protecting the seasonal path (a weekday bucket needs enough samples to be a baseline of its own), so that code shipped real and permanently unexercised. `app/data/seasonal_costs.json` is ten weeks built for it — deterministically generated by `scripts/make_seasonal_fixture.py`, `--check`-able against the committed file so it cannot rot silently — and its point is a contrast rather than a number: a planted Saturday on `analytics-batch` that is an ordinary Tuesday's figure and an impossible Saturday's, invisible inside a pooled bimodal baseline and obvious the moment Saturdays are compared with Saturdays. **Incident reports** export as shareable Markdown (`GET /actions/{id}/report`)
- **Brain room** in the dashboard (`/brain`) — insights, self-review, routines, runbook search, the backtest table and operator sign-in, wired live

## What It Does / What It Deliberately Does Not

The whole contract on one table — the right column is design, not backlog:

| ✅ Does | 🚫 Deliberately does not |
|---|---|
| Detects cost & security anomalies over a rolling baseline (z-score / MAD, weekly seasonality, min-history discipline) | Connect to real cloud providers — synthetic data by design; the detection pipeline is source-agnostic |
| Reasons about every cost signal with AI agents: evidence-cited triage, two remediation options with risk + rollback, adversarial review of contested calls | Let a model invent numbers — every figure the operator acts on is deterministic Python arithmetic, post-checked ±5% against the narrative |
| Files proposals into a human decision inbox with rationale + actor capture and a hash-chained audit trail that `GET /audit/verify` recomputes from genesis instead of asserting; a decided incident can be **really dispatched** to an operator-configured webhook, delivery recorded in the audit detail | Execute anything on real infrastructure — the infrastructure mutation is simulated by design, and nothing runs unapproved |
| Folds a repeating signal into the open card already asking about that service, counted and dated, so one unanswered question does not become five | Discard the repeats, or fold anything into a card a human has already decided — that would apply an old verdict to a new fact |
| Scores payment events with published, hand-reproducible rules (per-rule point attribution) — an experimental lane showing the governance rails generalize past cost & security | Run ML fraud models, auto-block payments, hide the scoring arithmetic, or present fraud as the core product |
| Remembers operator verdicts and feeds them back into future recommendations, disclosing how many were considered | Learn silently — memory use is visible on the card, and the chain's execution is traced hop by hop |
| Accounts for its own AI spend (call ledger, cache hits, fallbacks, quota view) under a per-run call budget | Burn quota unbounded, retry forever, or fail when the LLM is unavailable — every agent degrades to a labeled rule-based fallback |
| Ships hardened: strict CSP with self-hosted docs, security headers, rate-limited pulse and sign-in, idempotent decisions, JSON failure envelope, local auth (salted PBKDF2, roles) that gives every decision a server-derived operator identity, an outbound guard that will not let a configured URL aim back inside, a boot-time configuration audit that is fatal under `SENTINEL_ENV=production`, and a correlation id on every response and every log line | Ship Postgres, a job queue, real cloud adapters or Slack — deliberate boundaries of this build, not oversights; the standing watch is one opt-in daemon thread running a serial loop, not a scheduler |

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
| **SecOps operator** | SIEM dashboards, IAM audit logs, ticket queues | *"Who decided what, and can I prove it?"* | Human-in-the-loop state machine with idempotent decisions, the append-only decision ledger, and security signals flowing through the same detection pipeline (shipped with the Sprint 3 core pulled forward) |

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
    CLOUD[("☁️ cloud cost &amp; security data<br/>bundled fixtures by default · env-gated live lanes:<br/>self-telemetry / billing CSV import / external JSON feeds")] --> DET

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
│   ├── reflex.py         reflex engine — measured latency + reflex-rule drafts
│   ├── analyst.py        Analyst agent — triage, evidence, reflection, uncertainty
│   ├── recommender.py    Recommender agent + debate-lite skeptic
│   ├── debate.py         review panel — three seats, dissent and abstentions
│   ├── chronicler.py     Chronicler agent — pulse briefings
│   ├── security.py       security lane — same detection line, own event kind
│   ├── stream.py         simulated live tape — env-gated synthetic ticker
│   ├── feeds.py          data-source resolution — fixtures, self-telemetry, external feeds
│   ├── netguard.py       outbound guard — no feed or webhook may aim back inside
│   ├── fraud.py          fraud lane — published deterministic rule score
│   ├── actions.py        human-in-the-loop action lifecycle incl. reopen + suppression
│   ├── history.py        append-only lifecycle trail — the desk's per-card timeline
│   ├── ledger.py         hash-chained audit seal + /audit/verify
│   ├── decisions.py      decision memory retrieval + ledger export
│   ├── analytics.py      funnel, savings, trend/forecast/ROI, decision quality, receipts
│   ├── telemetry.py      self-request counters + run-receipt assembly
│   ├── insights.py       decision brain — history synthesis + HITL-safe self-review
│   ├── routines.py       saved read-only analysis playbooks + routines agent
│   ├── runbooks.py       curated runbooks — keyword retrieval (RAG-lite) + hit rate
│   ├── market.py         market watch — curated catalogue costed against the estate
│   ├── enrichment.py     blast-radius tiers, ATT&CK / FinOps refs, verification plans
│   ├── auth.py           local identity — salted PBKDF2, roles, session tokens
│   ├── bus.py            agent bus — persisted inter-agent feed
│   ├── pulse.py          one-call end-to-end chain + persisted last run
│   ├── watchdog.py       opt-in standing watch + its own vitals, /ready verdict
│   ├── ops.py            env-gated demo reset, watch health, executable preflight
│   ├── configcheck.py    boot-time configuration audit — fatal under production
│   ├── logstream.py      tagged log grammar — correlation id, opt-in JSON format
│   ├── metrics.py        Prometheus text exposition of what the app already counts
│   ├── dispatch.py       execute webhook delivery, recorded in the audit detail
│   ├── evalset.py        golden-set eval cases + scorers
│   ├── llm.py            provider layer: Gemini, context-aware fake, fallbacks, budget
│   ├── db.py             SQLite core — WAL, idempotency, seed-on-startup
│   ├── models.py         Pydantic schemas
│   └── data/             mock datasets — cost, security, payments, seasonal, inventory
├── configs/              mission YAMLs — finops, security, fraud (three postures)
├── static/               dashboard — tokenized design system, 5 palettes, vendored Swagger UI
├── scripts/              smoke sweep, failure drill, benchmark, release verification,
│                         billing import, seasonal fixture generator, Gemini spike
├── tests/                1288 pytest cases incl. performance budgets and the
│                         route-discovering contract suites
├── docs/                 architecture, ADRs, limitations, SLO, data dictionary
├── Makefile              setup / run / test / demo / smoke / drill
└── ProjectManagement/    sprint evidence packs (boards, screenshots)
```

## How to Run (Local)

Two commands to a running product:

```bash
make setup && make run        # or: make demo — fake provider, fresh dates, reset armed
make smoke                    # (other shell) 27-step PASS/FAIL sweep of the live chain
```

Before a release, the counters check themselves instead of a human checking
them at midnight:

```bash
bash scripts/verify_release.sh                  # counters + every relative link
bash scripts/verify_release.sh https://<host>   # ...plus the live link
```

It counts the suite with `pytest --collect-only` and the API surface from the
app's own OpenAPI document, reads every "N tests / N endpoints" claim out of
the shipped docs and fails on any that disagrees (sprint records are
recognised and left alone), and follows every relative markdown link. Given a
URL it also confirms the host is serving *our* app, that the deployed surface
matches this checkout, that the security headers are on the live response and
that the standing watch is still beating.

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

### Deployment modes

One deploy target, two honest postures — pick one per link, don't mix:

- **Showcase mode** — `SENTINEL_READONLY=1`: a public link that survives
  strangers' clicks. Every write (login included) is blocked while the
  panels keep reading, and the opt-in watchdog
  (`SENTINEL_WATCH_INTERVAL_SECONDS`) keeps the estate refreshing itself.
- **Live-ops mode** — `SENTINEL_REQUIRE_APPROVER=1` plus the bootstrap pair
  `SENTINEL_ADMIN_USER` / `SENTINEL_ADMIN_PASSWORD`: the team decides on the
  live link. The three decision verbs (approve / reject / execute) demand a
  signed-in approver or admin; reads stay public and registration stays open
  (self-registered accounts are viewers, so strangers can look but never
  decide). The bootstrap admin is recreated on every ephemeral-disk boot,
  never overwriting an existing user and never logging the password.
- In either mode, `SENTINEL_EXECUTE_WEBHOOK_URL` optionally delivers each
  executed incident report to your own webhook, with the delivery outcome
  recorded in the audit detail. The target is checked before the socket
  opens — https, public destination, no redirect-following — so a webhook
  or feed URL cannot be aimed back inside the trust boundary.
- Whichever posture the link runs, `SENTINEL_ENV` decides how loudly a gap
  is reported. `production` (or `prod`) makes every finding of the boot
  audit fatal: open writes, a missing or short-passworded bootstrap admin,
  the fake provider about to answer real users, or the outbound escape
  hatch left open, and the app refuses to start rather than serve a demo
  posture. Any other value — the live link runs `render` — logs the same
  findings as `[CONFIG]` and boots unchanged.
- Two operational knobs for a real deployment, both off by default:
  `SENTINEL_LOG_FORMAT=json` turns the tagged stream into one JSON object
  per line for a log pipeline, and `GET /metrics` is already there for a
  Prometheus scrape. `GET /ops/preflight` answers, in one call, whether
  this instance is actually ready to be shown.

## Built With

| Technology | Purpose |
|---|---|
| **Python 3.12** | Core language (pinned in venv, CI and Docker) |
| **FastAPI + Uvicorn** | REST API and ASGI server |
| **Pydantic v2** | Typed request/response models and validation |
| **pytest + httpx** | Automated test suite (1288 tests, incl. performance budgets and the endpoint contract suites below) — **96% line coverage** over `app/` + `main.py` (`make coverage`) |
| **Hypothesis** | Property-based tests: generated NaN / duplicate / extreme / reversed-window inputs against the detector |
| **bandit + pip-audit** | The security product scans its own source and its own dependencies (`make audit`); both gate CI |
| **SQLite** (stdlib `sqlite3`) | WAL-mode persistence core: action lifecycle, decision memory, LLM cache, idempotency |
| **Docker** | Containerized, deployment-ready packaging |
| **Gemini** (`google-genai`) | LLM provider layer with quota-aware retry and rule-based fallback |
| **Miro** | Scrum board and product backlog (official bootcamp template) |

Most of the suite tests one organ deeply. Two suites instead hold the whole
HTTP surface at once, and they **discover the endpoints themselves** by
walking `app.routes`, so a route added tomorrow is enrolled the moment it is
mounted rather than when someone remembers. The first pins the house rules on
every endpoint: a write verb answers 403 while `SENTINEL_READONLY=1` and no
read is blocked by it, an id that does not exist answers 404 instead of 500,
a malformed body answers 422 in the project's one-key failure envelope, every
response carries `X-Request-ID` and the security headers (taken from
`main.py`'s own constants, so tightening them there tightens the test), and
no response leaks a traceback or a filesystem path. The second pins the
published OpenAPI document, which nobody edits and therefore nobody notices
rotting: a summary and real prose on every operation, every JSON success
resolving to a declared model, a closed tag vocabulary, a documented 404 on
every id-addressed operation and the state machine's 409, and a digest of
path + method + status codes asserted inline so any change to the surface
arrives as a reviewable diff. Where an endpoint genuinely answers something
else it is named individually with its reason and its exact expected status —
never waived by a relaxed assertion, because a waiver that stops being true
has to fail too.

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
| Continuous integration — tests on every push | Sprint 2 → 3 | ✅ shipped — [`ci.yml`](.github/workflows/ci.yml) runs ruff + the full suite on every push and PR, plus a second `audit` job: bandit over our own source and pip-audit over the dependencies that ship |
| Dashboard palette revision after UI reference research | Sprint 2 → 3 | ✅ shipped — five-palette switcher (horizon default · night · paper · dawn · vivid), persisted |
| Operability — correlation ids, JSON logs, `/metrics`, watch vitals, executable preflight | Sprint 3 | ✅ shipped — [`app/logstream.py`](app/logstream.py) · [`app/metrics.py`](app/metrics.py) · [`app/watchdog.py`](app/watchdog.py) · [`app/ops.py`](app/ops.py) |
| Hardening — hash-chained ledger, boot configuration audit, outbound SSRF guard | Sprint 3 | ✅ shipped — [`app/ledger.py`](app/ledger.py) · [`app/configcheck.py`](app/configcheck.py) · [`app/netguard.py`](app/netguard.py) |
| Deployment — Render, read-only public link | Sprint 3 | ✅ shipped — the live `/health` answers `env render · provider fake · readonly True`, the standing watch is beating and the deployed surface matches this checkout (59 endpoints), all four checked by `scripts/verify_release.sh <url>` |
| Live Gemini key spike (real RPM/RPD measurement) | Sprint 3 | planned |
| User's-eye UX pass — gaps, friction and flow measured from the operator's seat | Sprint 3 | planned |
| UptimeRobot monitor, live demo walkthrough & 3-minute product video | Sprint 3 | planned |

## Requirements Compliance

Mapping of the official bootcamp scrum-notebook requirements to their evidence in this repository:

| Requirement | Status | Evidence |
|---|---|---|
| Team name & roles documented | ✅ | [Team Name](#team-name) · [Team Members](#team-members) |
| Product name, description, features, target audience | ✅ | [Information About the Product](#information-about-the-product) |
| Product Backlog board (Miro) | ✅ | [Product Backlog URL](#product-backlog-url) |
| Sprint Notes (never left empty) | ✅ | [Sprint 1](#sprint-1) · [Sprint 2](#sprint-2) |
| Point estimates & completion logic | ✅ | [Sprint 1](#sprint-1) · [Sprint 2](#sprint-2) |
| Daily Scrum documentation | ✅ | Sprint 1: [Slack & WhatsApp](ProjectManagement/Sprint1Documents/) — Sprint 2: [WhatsApp](ProjectManagement/Sprint2Documents/whatsapp_daily_scrum.png) · [Slack huddle](ProjectManagement/Sprint2Documents/slack_huddle.png) |
| Sprint board screenshots | ✅ | Sprint 1: [Miro board](ProjectManagement/Sprint1Documents/miro_board.jpeg) · [burndown](ProjectManagement/Sprint1Documents/burndown_sprint1.png) — Sprint 2: [Miro board](ProjectManagement/Sprint2Documents/miro_board_sprint2.png) · [burndown](ProjectManagement/Sprint2Documents/burndown_sprint2.png) |
| Product status screenshots | ✅ | Sprint 2: [broadsheet](ProjectManagement/Sprint2Documents/broadsheet_sprint2.png) · [Swagger (46)](ProjectManagement/Sprint2Documents/swagger_sprint2.png) — Sprint 1: [dashboard](ProjectManagement/Sprint1Documents/dashboard.png) · [Swagger](ProjectManagement/Sprint1Documents/swagger_docs.png) |
| Sprint Review & Retrospective | ✅ | [Sprint 1](#sprint-1) · [Sprint 2](#sprint-2) |
| Working product increment | ✅ | [`GET /anomalies`](main.py) · agent chain ([analyze](app/analyst.py) · [recommend](app/recommender.py) · [pulse](app/pulse.py)) · [HITL lifecycle](app/actions.py) · [tests](tests/) |

## Scope & Limitations (By Design)

> **The full, current list lives in [docs/LIMITATIONS.md](docs/LIMITATIONS.md)** —
> twelve sections covering what is synthetic, what is simulated, what is
> estimated rather than measured, and what we never got round to verifying.
> If anything in the demo looks like it does more than that page admits,
> that page is the one to believe.

These constraints are intentional Sprint 1 decisions, not oversights:

- **Synthetic mock data only** — real cloud-provider connectors are outside the
  competition scope; the detection pipeline is data-source agnostic by design.
- **Human-in-the-loop lifecycle landed in Sprint 2** — `GET /actions` plus
  `POST /actions/{id}/approve|reject` (idempotent via `Idempotency-Key`)
  implement the operator decision gate; nothing ever executes without an
  approval, and execution stays simulated
  (see [docs/architecture.md](docs/architecture.md)).
- **Security and fraud lanes landed with the Sprint 3 core** — mock security
  events ride the identical detection line (own mission and event kind),
  scored deterministically with no LLM agent; the **fraud lane is
  experimental**, a third source proving the same governance rails
  generalize rather than a core product line. Both are operator-facing
  suggestions, never agent conversations or automatic blocks.
- **The public link is a read-only showcase** — the app is containerized
  (non-root, healthchecked) and deployed from `render.yaml`; the live
  `/health` reports `env render · provider fake · readonly True`, so every
  write is refused, no key and no quota are involved, and the synthetic data
  may reset on redeploy. The opt-in watchdog keeps the estate refreshing
  itself and reports on its own heartbeat; `scripts/verify_release.sh <url>`
  confirms the host is serving *our* app, that its surface matches this
  checkout, that the security headers are on the live response and that the
  watch is still beating.
- **Storage is sqlite3 on an ephemeral disk** — the hash chain proves the
  history was not rewritten, it does not make the history survive a restart.
  Identity is local (`/auth`, PBKDF2, four roles) rather than OIDC/SSO, and
  there is no tenant isolation.

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
  - The LLM layer was built provider-agnostic: a deterministic fake provider (`SENTINEL_FAKE_LLM=1`) drives all tests and offline demos, and the rule-based fallback path keeps every endpoint answering even with the LLM unavailable. Through Sprint 2 the whole chain ran on the fake provider; the live Gemini key will be provisioned from a billing-disabled project in Sprint 3, so the quota-safety posture stays zero-cost by construction.
  - Quota discipline was locked early: responses are cached, reflection runs only on critical signals, and the debate-lite skeptic costs at most one extra call per decision.
  - The Recommender's prompt interface was frozen mid-sprint so decision memory could be injected later as a single isolated change — which is exactly how it landed.
  - Money figures shown to the operator are computed deterministically in Python; the model narrates, it never invents numbers.
  - Execution stays simulated by design: the state machine records an executed action with a SIMULATION marker and no real infrastructure is ever touched.
  - The dashboard was rebuilt on a tokenized design system with a persisted four-palette switch — **horizon** (the night-blue default shown in the Sprint 2 captures, renamed from cobalt at sprint close), joined by night, paper and dawn — now letting the team and reviewers flip palettes live (night mode included). The final palette decision (retro action item, owner: Tuana) lands at the Friday design session.
  - Mid-sprint hardening from the July 12 review requests: interactive ledger tables (sortable signals, click-to-filter cost rows), monotone-curve charts, performance budget tests over the mock-data pipeline, and a regression fix that restored Swagger UI under the strict CSP.
  - The sprint's second week pulled the **Sprint 3 core forward**: detection quality (rolling baseline, MAD, weekly seasonality), the mission DSL + reflex engine, the unified security and fraud lanes, the guardrail pack (call budget, timeout, numeric post-check), the operations-intelligence analytics, the chronicler agent, the persisted agent trace and finally the **agent bus with a live feed panel** — the whole inter-agent conversation streaming into the dashboard as it happens.
  - The fraud lane is developed in this repository as published deterministic rule arithmetic (no ML); its strongest signals and a projected budget overrun now file cards into the same human decision inbox — three missions, one decision box.
  - The CI restore closed at sprint end: the token-scope block turned out to apply only to HTTPS token pushes, so the workflow shipped over an SSH push — [`ci.yml`](.github/workflows/ci.yml) now runs ruff and the full suite on every push and PR.

- **Expected point completion within the sprint**: 13 points

- **Point Completion Logic**: Sprint 2 carries 13 of the 36 total backlog points: Gemini agent spike (2), Analyst agent (3), Recommender with debate-lite (3), human-in-the-loop lifecycle (3), decision memory (2). All five stories are code-complete as of July 12 — the suite has since grown to 446 automated tests (~9s on the fake provider) — and formal completion is assessed at the July 19 review and demo.

- **Backlog order and story selections**: The Sprint 2 backlog is ordered by dependency — the Gemini agent spike first (it unblocks both agents), then the Analyst and Recommender agents, the human-in-the-loop lifecycle, and finally decision memory feeding the Recommender. Each estimate is kept below half the sprint total (at most 3 of 13). Stories are split into tasks on the Miro board and assigned across the four active team members; on the board blue cards are user stories and red/orange cards are tasks (legend on the board itself).

- **Daily Scrum**: daily communication runs over WhatsApp with team meetings and huddles on Slack — evidence captured through the sprint:

  ![WhatsApp — daily coordination and meeting scheduling](ProjectManagement/Sprint2Documents/whatsapp_daily_scrum.png)

  ![Slack — the team in a 1h 36m huddle](ProjectManagement/Sprint2Documents/slack_huddle.png)

  More Slack / WhatsApp evidence: [scrum 1](ProjectManagement/Sprint2Documents/slack_dashboard_share.png) · [scrum 2](ProjectManagement/Sprint2Documents/slack_frontend_share.png) · [scrum 3](ProjectManagement/Sprint2Documents/whatsapp_product_share.png)

- **Sprint board update**:

  ![Sprint 2 Burndown](ProjectManagement/Sprint2Documents/burndown_sprint2.png)

  ![Miro Scrum Board — Sprint 2](ProjectManagement/Sprint2Documents/miro_board_sprint2.png)

  Detail — Done column with per-member owners: [part 1](ProjectManagement/Sprint2Documents/miro_board_sprint2_done_column.png) · [part 2](ProjectManagement/Sprint2Documents/miro_board_sprint2_done_column_2.png) · [part 3](ProjectManagement/Sprint2Documents/miro_board_sprint2_done_column_3.png).

  The committed 13 points were code-complete by July 12 — about a week ahead of the sprint deadline — so the actual line sits below the ideal for the whole sprint; the second week was then spent pulling the Sprint 3 core forward as bonus. On the board, blue cards are user stories and red/orange cards are tasks (legend on the board itself).

- **Product Status — the running increment at sprint close**: a single full-page capture of the `/broadsheet` view (every room on one page) on mock data, horizon palette. One `POST /pulse` (input) drives the cost chain; the anomaly feed, cost ledger, investigation and decision inbox below are the output of that call plus one operator approval:

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

  ![CloudSentinel Sprint 2 — broadsheet, every room on one page](ProjectManagement/Sprint2Documents/broadsheet_sprint2.png)

  Both planted cost spikes are detected (compute z=3.61, database z=3.60), triaged REAL and filed as proposals; the compute action was approved and executed — SIMULATION, the database action still awaits the hand. The same sheet runs the unified security lane (2 signals) and the experimental fraud lane (3 holds) through the same governance rails and decision inbox. The hero / top of the sheet in close-up: [dashboard hero](ProjectManagement/Sprint2Documents/dashboard_sprint2.png). Palette directions for the design decision: [night](ProjectManagement/Sprint2Documents/dashboard_night.png) · [paper](ProjectManagement/Sprint2Documents/dashboard_paper.png).

  ![CloudSentinel API — self-hosted Swagger, 46 endpoints](ProjectManagement/Sprint2Documents/swagger_sprint2.png)

  The full API surface at sprint close is **46 endpoints** (self-hosted Swagger, no CDN). The committed Sprint 2 scope was the agent-layer subset — analyze / recommend / actions / decisions / pulse, [13 endpoints captured mid-sprint](ProjectManagement/Sprint2Documents/swagger_13_endpoints.png); the second-week bonus (security / fraud / missions / analytics / chronicler / agent bus) and the July-19 decision-brain groundwork grew it to 46.

- **Sprint Review**: Sprint 2 closed with all five committed stories completed (13/13 points), demoed end to end at the July 19 review over `POST /pulse`: detect → Analyst triage with cited evidence → debate-lite skeptic → Recommender options with Python-computed savings → decision inbox → operator verdict → decision memory. Everything ran on the deterministic provider (the live Gemini key is deferred to Sprint 3, to be provisioned from a billing-disabled project), which is itself the demo's point: the agent layer degrades honestly and never blocks on quota. Beyond the committed scope, the second week of the sprint pulled the Sprint 3 core forward — detection-quality controls, the mission DSL and reflex engine, the unified security and fraud watch, the guardrail pack, the operations-intelligence analytics, the chronicler agent, the persisted agent trace, the live agent-feed panel, cross-lane HITL cards (fraud holds and the budget guard) and self-hosted Swagger under one strict CSP — and, on July 19, a decision-brain groundwork layer (local identity + roles, history-synthesis insights, a HITL-safe self-review loop, saved routines, runbook retrieval, a detector backtest and an in-dashboard brain room) — growing the suite from 27 tests at Sprint 1 close to **446 tests over 46 endpoints**. Decisions taken: the fraud lane stays rule-based and in-repo; deployment (Render, `render.yaml` ready) and the live-key spike open Sprint 3; the final palette decision is carried into the Sprint 3 design session with the four-way switcher shipped.

  | Story | Points | Result |
  |---|---|---|
  | Gemini agent spike — provider layer, retry, fallback, fake provider | 2 | ✅ Completed |
  | Analyst agent — triage, cited evidence, reflection at critical z | 3 | ✅ Completed |
  | Recommender + debate-lite skeptic — two options, computed savings | 3 | ✅ Completed |
  | Human-in-the-loop lifecycle — approve / reject / simulated execute | 3 | ✅ Completed |
  | Decision memory — verdicts stored, retrieved and fed to the agent | 2 | ✅ Completed |
  | Bonus: Sprint 3 core pulled forward (detection quality · missions · lanes · guardrails · analytics · chronicler · agent bus) + July-19 decision-brain groundwork (identity · insights · self-review · routines · runbooks · backtest · brain room) | — | ✅ Delivered |
  | **Total** | **13 / 13** | |

- **Sprint Review Participants**: `Tuana Aydın, Muratcan Ateş, Çağla Yurtseven, Mert Kurt`

- **Sprint Retrospective**:
  - **What went well**: the fake-provider discipline let the entire agent layer land and demo with zero LLM spend; freezing the Recommender's prompt interface early meant decision memory landed later as a single isolated change, exactly as planned; the July 12 mid-sprint review requests were absorbed without breaking stride; pulling the Sprint 3 core forward leaves the final sprint free for deployment, the video and polish; the suite grew 27 → 446 tests with ruff clean on every commit.
  - **What to improve**: the second-week push concentrated commits on one member — the pull-request rhythm from Sprint 1 (every member ships reviewed PRs) slipped and must return; CI landed only at the very end of the sprint — the token-scope block turned out to apply only to HTTPS pushes — so for most of the sprint green runs lived only on developer machines; the live Gemini key was not provisioned during the sprint, so real-quota behavior (RPM/RPD) remains unmeasured.
  - **Action items**: Sprint 3 work is distributed as reviewed PRs across all four members from the board; the Gemini key spike (`scripts/spike_gemini.py`) is the first task of Sprint 3; CI stays green and grows browser E2E plus a post-deploy smoke during Sprint 3; Render deployment closes before the July 25 gate with UptimeRobot on `/health`; the final palette decision is taken at the Sprint 3 design session (owner: Tuana).

---

# Sprint 3

*The final sprint runs July 20 – August 2 and closes with deployment, the live demo and the 3-minute product video.*

- **Head start (all of this shipped inside Sprint 2)**: Sprint 2's second week pulled the Sprint 3 technical core forward — detection quality (rolling baseline / MAD / weekly seasonality), the mission DSL + reflex engine, the **security lane through the identical detection line**, the fraud rule-score lane with cross-lane HITL cards, the guardrail pack, operations-intelligence analytics, the chronicler agent, the agent bus with its live feed panel, self-hosted Swagger and the demo-operations knobs (date rebase, demo reset, read-only showcase) — and, on July 19, a decision-brain groundwork layer (local identity + roles via `/auth`, history-synthesis insights, a HITL-safe self-review loop, saved routines, runbook retrieval, a detector backtest and an in-dashboard **brain room**). All of it landed inside Sprint 2's dates and is counted in the Sprint 2 bonus above; **Sprint 3 has not started yet**, so nothing here is claimed as Sprint 3 delivery — the final sprint opens focused only on the items below.

- **Closeout**: the last two days before the gate — the ordered task list with owners, the honest answer on live data, and the submission checklist — is **[docs/CLOSEOUT_48H.md](docs/CLOSEOUT_48H.md)**.

- **Backlog**: the full prioritized working list — committed competition scope, the hardening backlog from the July 18 engineering review, the freeze list and the definition of done — lives in **[docs/sprint3_backlog.md](docs/sprint3_backlog.md)**; stories are cut onto the Miro board from there.

- **Remaining scope** (headline items — the backlog holds the detail):
  - **Live Gemini spike** — provision the billing-disabled key and measure real RPM/RPD with `scripts/spike_gemini.py`; the whole chain already runs on the deterministic provider, so this lights up narratives, not correctness.
  - **Continuous integration** — ✅ landed at Sprint 2 close: [`ci.yml`](.github/workflows/ci.yml) runs ruff + the full suite on every push and PR (580 cases then, 1190 now), plus an `audit` job running bandit over our own source and pip-audit over the dependencies that ship; Sprint 3 grows it with browser E2E and a post-deploy smoke.
  - **Deployment** — ✅ live on Render from `render.yaml` (non-root, healthchecked image) with `SENTINEL_READONLY=1` on the public link and the dashboard's LIVE banner on via `SENTINEL_ENV=render`; the deployed surface matches this checkout and the standing watch is beating. UptimeRobot on `/health` is still to be wired.
  - **Live-data trial & market watch** — a credential-free real billing export through the source-agnostic loader, and the trend/news-driven "possible suggestions" table.
  - **User's-eye UX pass** — friction measured from the operator's seat; the palette switcher shipped with horizon as the default and a fifth, `vivid`, for the control surface; EN/TR overview kept in sync ([Türkçe özet](docs/README.tr.md)).
  - **Evidence & submission** — sprint documents, the 3-minute product video, and the August 2 form.

---

# Field Guide — Sixty Seconds to a Decision

1. **Run it** — `uvicorn main:app --reload`, open `http://127.0.0.1:8000/` (Swagger at `/docs`).
2. **Tune the watch** — drag the sensitivity slider or pick a service; section I re-scans live. Prefer the dark room? Flip the palette to **night** in the control rail — the choice persists.
3. **Investigate** — hit *investigate →* on a signal: evidence sparkline, baseline, deviation, then *run analyst agent →* for triage with cited rows.
4. **Watch them talk** — open the **agent feed** rail (bottom right): every hop of the chain — pickups, handoffs, skeptic challenges, verdicts, briefings — streams in live as it happens.
5. **Decide** — *file recommendation →*, type a rationale, then approve or reject in the inbox. Execution of the infrastructure change is always a simulation — though with `SENTINEL_EXECUTE_WEBHOOK_URL` set, the decided incident report really ships to your webhook — and the ledger remembers every hand that touched it.
6. **Check its work** — `GET /audit/verify` recomputes the decision chain from genesis and tells you where it breaks (it should say nothing broke); `GET /analytics/quality` says how the desk is doing rather than how the model is doing; `GET /analytics/receipts` itemises what that pulse actually cost; `GET /ops/preflight` answers whether this instance is fit to be shown at all.

# In Short

CloudSentinel closes the gap between *"your cloud bill spiked"* and *"someone accountable did something about it"*: a deterministic detector finds the spike, AI agents explain it and propose two ways out with computed savings, a skeptic challenges weak calls — critical ones face a three-seat review panel — and nothing executes until a human says so, in writing, forever.

# Acknowledgements

- **Yapay Zeka ve Teknoloji Akademisi** — for the YZTA Bootcamp 2026 program, the scrum template and the mentoring hours behind this repo.
- **Team CloudSentinel** — every feature here crossed at least one teammate's review before it landed.
- **The open tools that carried us** — FastAPI, Pydantic, pytest, SQLite, Docker, Gemini (`google-genai`), and the Google Fonts faces (Instrument Serif, Jacquard 24, UnifrakturMaguntia) that give the dashboard its voice.
- **Michelangelo** — for the two hands we borrowed; the machine watches, the human decides.

---

<img src="docs/img/banner_hands.png" alt="every action awaits a human hand" width="100%" />

<div align="center"><sub>Built by Team CloudSentinel — YZTA Bootcamp 2026 · AI Track · Group 60</sub></div>
