# Service Levels — what we measure, and what we promise

*An SLI is a number the system already produces about itself. An SLO is the
line we agreed to hold it above. This page names both, plus the endpoint or
test that measures each one, so every promise here can be checked rather than
believed.*

Companions: [`DATA_DICTIONARY.md`](DATA_DICTIONARY.md) for what the numbers
mean, [`LIMITATIONS.md`](LIMITATIONS.md) for what this build deliberately
does not promise.

## Scope, honestly stated

These targets describe **this build on its current footprint**: one process,
SQLite on an ephemeral disk, a free-tier host that sleeps, and a synthetic
estate of a few services over weeks of data. They are real measurements, not
aspirations — but a target met at fixture scale is a target met at fixture
scale, and the [growth path](#growth-path) below says what each one would
need to survive real volume. Naming the footprint is part of the promise.

---

## Availability and freshness

| # | SLI — what is measured | SLO — the line | Measured by |
|---|---|---|---|
| A1 | `/health` answers 200 | 99% of probes over a rolling week, **excluding** free-tier cold starts | `scripts/smoke.sh`, external uptime probe |
| A2 | `/ready` reports `status != "unready"` — database reachable, mission config parses, dataset loads | 99% of probes | `GET /ready`, smoke step |
| A3 | **Watch freshness**: age of the newest `pulse_log` row against the configured cadence | Under **2× `SENTINEL_WATCH_INTERVAL_SECONDS`**; beyond it the instance reports `degraded` | `GET /ops/health/watch` |
| A4 | Cold-start time to first answer on the free tier | Under **90 s** | `curl --max-time 90`, documented in the pre-flight |

**A3 is the one that has already failed.** On 1 August the deployed watch
stopped beating at 20:40 and nothing said so for three hours, because
`/health` correctly answered 200 the whole time — the process was fine. That
is why freshness is its own indicator with its own endpoint, and why the
answer is `degraded` rather than 503: a slipped heartbeat must not pull the
public showcase out of rotation. Reporting the fault must never cost more
than the fault.

---

## Latency

Budgets are enforced as tests, so a regression fails CI rather than being
noticed on stage. All figures are on the deterministic provider — the live
provider's latency is the network's, not ours.

| # | SLI | SLO | Measured by |
|---|---|---|---|
| L1 | Deterministic reflex scan, p95 | **< 25 ms** | `test_reflex_p95_budget` |
| L2 | Reflex scan at estate scale (8 services × 365 days), p95 | **< 80 ms** | `test_reflex_p95_budget_at_estate_scale` |
| L3 | `GET /anomalies` through the whole HTTP layer, p95 | **< 250 ms** — half the 500 ms interactive ceiling | `test_http_scan_p95_budget` |
| L4 | Read endpoints in bulk (`/anomalies`, `/costs/summary`, `/costs/daily`) | **< 100 ms** each on average | `test_detection_scan_budget`, `test_cost_aggregation_budget` |
| L5 | Full `POST /pulse` chain — detect → analyst → debate → recommender → inbox | **< 3 s** | `test_pulse_full_chain_budget` |
| L6 | CSV export | **< 150 ms** per export | `test_csv_export_budget` |
| L7 | Reflex scaling shape as records grow ~52× | Sub-quadratic — under 10× the record ratio | `test_reflex_scaling_stays_roughly_linear` |

L1–L3 are percentile gates; L4–L6 are total-of-N budgets, which are means in
disguise and can hide one pathological call. That is why the paths that make
a claim out loud — the reflex figure the dashboard prints, and the scan a
juror actually waits on — are gated on p95 and publish `max` beside it.

---

## Correctness and delivery

| # | SLI | SLO | Measured by |
|---|---|---|---|
| C1 | **Critical card delivery** — a critical signal in the data produces exactly one decidable card | **100%**, and never a second card for the same `(kind, service, date)` | UNIQUE natural key on `events`; `test_suppression.py`, `test_anomalies.py` |
| C2 | **Nothing executes unapproved** — every `executed` action has an `approved` predecessor by a named actor | **100%**, no exception | CHECK constraint on `actions.state` + `test_actions.py`, `test_guardrails.py` |
| C3 | **Idempotent decisions** — a replayed `Idempotency-Key` returns the stored answer and performs no second effect | **100%** | `test_resilience.py`, `test_dispatch.py` |
| C4 | **No generated money** — every currency figure in a payload is Python arithmetic | **100%** | `test_contracts.py` numeric checks |
| C5 | Malformed feed records reaching a detector | **0** — every drop is counted rather than silent | `_validate_*` in `app/feeds.py`; `test_resilience.py` |
| C6 | Detector precision/recall on the labelled fixture | Published, not hidden — the number is whatever it is | `GET /metrics/backtest` |

---

## Durability and audit

| # | SLI | SLO | Measured by |
|---|---|---|---|
| D1 | **Audit chain verifies** — the hash chain over sealed records is unbroken | **100%** while the process lives | `GET /audit/verify` |
| D2 | A verdict, once recorded, is never rewritten or deleted by any product path | **100%**; only `POST /ops/demo-reset` clears state, and only when explicitly armed | Append-only `action_events`; `test_ops_pack.py` |
| D3 | AI-spend history survives a demo reset | **100%** — `ai_usage` and `llm_cache` are excluded from the wipe by name | `test_demo_ops.py` |
| D4 | Decision history survives a **restart** | **Not promised.** SQLite on an ephemeral disk: a restart clears it, by locked decision | [`LIMITATIONS.md`](LIMITATIONS.md) |

D4 is listed precisely because it is a gap. An SLO page that only lists
targets it meets is marketing.

---

## Degradation — what happens when something is down

The promise is not that these never happen; it is that each one has a defined
behaviour rather than an outage.

| Failure | Behaviour | Verified by |
|---|---|---|
| LLM provider unavailable, timed out, or out of quota | Every agent falls back to its rule-based answer, tagged `source: "fallback"`; cards are still filed | `test_resilience.py` |
| External feed down | Last good payload, then the bundled fixture; `/health` reports `mock (feed unavailable)` rather than claiming live data | `test_resilience.py`, `test_feeds.py` |
| Database contended | SQLite busy timeout makes the second writer wait (5 s); an exhausted timeout answers **503 + `Retry-After: 2`**, never a traceback | `test_resilience.py` |
| Standing watch stops beating | `/ready` reports `degraded` and stays 200; `/ops/health/watch` names the gap and the last error | `test_watchdog.py` |
| Webhook dispatch fails | The execute still succeeds — the state machine never depends on a webhook's mood — and the failure is recorded in the audit detail | `test_dispatch.py` |
| Unhandled error anywhere | JSON envelope, status 500, traceback to the log and never to the wire | `test_demo_ops.py` |

---

## How these get checked

```bash
make test        # ruff + the full suite, including every latency budget above
make smoke       # the live chain, PASS/FAIL, against local or the public link
make verify      # the counters in the docs against the code that backs them
curl -s localhost:8000/ops/preflight   # the demo checklist, executed
```

`GET /ops/preflight` is the operational form of this page: it runs the
dataset, mission, provider, write-posture, watch, freshness, disk, feed and
security-header checks in one call and answers with a single `ok`.

---

## Growth path

Each target above is met on the current footprint. What each would need to
survive real volume — the road out of the boundaries in
[`LIMITATIONS.md`](LIMITATIONS.md), not a claim that we have walked it:

| Target | What breaks first at scale | What it would take |
|---|---|---|
| A2 readiness | One process, one disk | Postgres with connection pooling; readiness gates on the pool, not a file |
| A3 freshness | An in-process thread dies with its process | A scheduler outside the request path (worker + queue), with the same freshness indicator over it |
| L1–L2 reflex latency | The scan is O(records) in memory | Windowed aggregates in the store; the p95 budget stays the gate |
| L4 pulse chain | Serial agent hops | Fan out per signal; the budget becomes per-signal rather than per-pulse |
| C1 delivery | The UNIQUE natural key is per-database | A tenant column in the key, once tenants exist |
| D1 audit | Chain verification walks every row | Periodic checkpoint anchors so verification is O(since last anchor) |
| D4 durability | Ephemeral disk | Managed Postgres with backups — the single change that converts D4 from a gap into a target |
