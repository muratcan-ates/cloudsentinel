# Sprint 3 Backlog — the working list

*Sprint 3 runs July 20 – August 2.* This is the single prioritized to-do
list for the final sprint, consolidated from the July 17 team meeting, the
July 18 planning notes and the July 18 engineering review of the repository.
Stories are cut onto the Miro board from here; owners are assigned at
sprint planning.

**How to read it:** section A is the committed competition scope — it ships
this sprint. Section B is the hardening backlog surfaced by the engineering
review — it enters the sprint only if A closes early, and otherwise stands
as the honest post-competition roadmap. Section C is explicitly frozen so
polish cannot crowd out substance. Section D is the end-state the backlog
is aimed at.

---

## A — Committed (competition scope, July 20 – August 2)

| # | Story | Notes | Status |
|---|---|---|---|
| A1 | **Live Gemini key spike** | Provision the billing-disabled key; measure real RPM/RPD, JSON-validity rate and latency with [`scripts/spike_gemini.py`](../scripts/spike_gemini.py); record results here and in the README | open |
| A2 | **Continuous integration** | `ci.yml` — ruff + full suite on every push and PR | ✅ landed July 18 |
| A3 | **Deployment** | Render (`render.yaml`, non-root healthchecked image) + UptimeRobot on `/health`; **the public link ships with `SENTINEL_READONLY=1`** so strangers' clicks cannot mutate shared state | open |
| A4 | **Live-data trial** | One provider, file-based and credential-free (bootcamp-safe): ingest a real cloud billing export (Azure Cost Management / AWS CUR CSV) through the existing source-agnostic loader behind a flag, and re-run the detection benchmark on it | open |
| A5 | **Market watch — "possible suggestions" table** | Trend / news / Reddit / X market tracking distilled into a curated opportunities table (e.g. pricing changes, new instance families, commitment discounts) rendered as an operator-facing suggestions card; research spike first, curated static dataset if live fetching falls outside competition rules | open |
| A6 | **New-technology scouting** | Survey of current techniques and repositories (saved YT/IG sources, NotebookLM research pack) for pieces that can join the project **without leaving bootcamp rules**; each candidate written up with a go / no-go | open — owner: Murat |
| A7 | **EN/TR language** | Turkish product overview (`docs/README.tr.md`) linked from the masthead; kept in sync with the English README at sprint close | ✅ first cut landed July 18 |
| A8 | **UX pass & final palette** | Friction measured from the operator's seat; final palette decision at the design session (four-way switcher already shipped); redesign of the flagged panel section's colors; demo placeholders labeled *sample narrative* or removed so fixture text can never read as live output | open |
| A9 | **Evidence & Miro** | New product visuals on the board, Sprint 3 board + burndown, `ProjectManagement/Sprint3Documents/` evidence pack through the sprint | open |
| A10 | **Video & submission** | The 3-minute product video and the August 2 submission form | open |

## B — Hardening backlog (from the July 18 engineering review)

Ordered as the review ordered them; each item names the gap it closes. These
are product-grade requirements, not competition requirements — they enter
Sprint 3 only after section A closes, and otherwise define the roadmap after
August 2.

1. **Real identity** — OIDC/OAuth login, server-derived operator identity
   (today the name is free text from the browser), `viewer / analyst /
   approver / admin` roles, tenant isolation; the audit trail becomes
   trustworthy only after this.
2. **Durable state** — PostgreSQL + migrations + backup; today an ephemeral
   restart erases decision memory and the audit ledger, which contradicts
   the product's own promise.
3. **Scheduled ingestion** — a background worker that watches a real source
   on a schedule, so the sentinel monitors instead of waiting for a click.
4. **Pulse as a background job** — `POST /runs` → `202 + run_id`, worker
   pipeline, `GET /runs/{id}` progress; removes the long-open synchronous
   request and enables cancellation and partial-failure handling.
5. **One real side effect** — approval creates a real GitHub/Terraform PR or
   Jira ticket, the external URL lands in the audit record, and a
   **verification step** re-checks cost afterwards: detect-to-resolution
   instead of detect-to-database.
6. **Detection upgrades** — leave-one-out / forecasting-residual scoring
   (today the anomalous point inflates its own baseline), backtesting with
   precision/recall, alert suppression + deduplication, and a fixture long
   enough to actually exercise weekly seasonality.
7. **Savings model realism** — resource-level math (SKU, utilization,
   region, commitments) with a confidence interval, replacing the
   `excess × 30 × containment%` heuristic.
8. **LLM evaluation set** — 50–100 labeled anomaly scenarios; triage
   accuracy, evidence precision, unsupported-claim rate, format-failure
   rate, P95 latency and cost per anomaly, measured against the live model
   (builds on A1).
9. **Shared-store rate limiting** — per-user/tenant limits in Redis or the
   database; the in-process, per-IP limiter resets on restart and does not
   protect a multi-instance deploy.
10. **Test depth beyond the fake provider** — browser E2E (Playwright),
    post-deploy smoke, live-provider contract tests, migration tests, load
    tests.
11. **Observability** — structured logs, metrics, tracing and error
    reporting, so the operations story holds outside the demo.

## C — Frozen for the competition window

The review's verdict was blunt: more polish no longer reduces the product's
main risk. Until section A is done, no work lands on:

- fraud-lane expansion (it stays published rule arithmetic, in-repo),
- new palettes or themes beyond the final palette decision,
- radar animation growth and new agent characters,
- additional analytics panels or "intelligence" metrics,
- new mission-DSL features.

## D — Definition of Done (north star)

The chain the backlog builds toward, kept from the review:

```text
real cost data arrives on a schedule
→ detector flags a real resource anomaly
→ context (usage, deployments, tags) is gathered
→ agents propose with verifiable evidence only
→ an authenticated, authorized human approves
→ a real ticket/PR is created and linked in the audit record
→ post-change cost is re-verified
→ history survives any restart
→ the whole path is proven by browser E2E in CI
```

Until that chain closes, the honest label stays the one the review gave the
project: a well-engineered prototype that successfully simulates the
behavior of a production product — with section A making it a winning demo
and section B making it a product.
