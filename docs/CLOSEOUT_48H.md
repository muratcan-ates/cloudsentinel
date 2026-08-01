# Closeout — the last 48 hours (August 1 → August 2)

Answers the two questions from the team thread: **what is left that fits in
two days**, and **what "live data / a working product" actually costs**.

Everything below is either a task with an owner and a time box, or an honest
statement of what we are not doing before the gate. The full sprint list
lives in [sprint3_backlog.md](sprint3_backlog.md); this page is only the
closeout.

**State at the time of writing (Aug 1, updated late night):** 1160 tests green,
ruff clean, CI on every push, 57 endpoints, six rooms, the agent chain and HITL
loop (now with reopen + per-card timeline) working end
to end on the deterministic provider. The gaps are not in the build — they are
deployment, the live-key measurement, and the submission pack.

---

## 1 · The two-day list

Ordered so that a dropped item costs the least. Times are working estimates,
not ceremony.

| # | Task | Owner | Est. | Blocks submission? |
|---|---|---|---|---|
| **T1** | **Deploy to Render** — connect the repo, apply `render.yaml` as-is (it already ships `SENTINEL_READONLY=1`, `SENTINEL_ENV=render`, fake provider), wait for the first green healthcheck, then run `scripts/smoke.sh` against the public URL | Murat | 45 min | **Yes** — the jury link is the product |
| **T2** | **UptimeRobot on `/health`** — 5-minute interval so the free instance does not cold-start during judging | Murat | 10 min | No, but a sleeping demo reads as a broken one |
| **T3** | **Put the live URL everywhere** — README masthead badge, submission form, video end card, `ALLOWED_ORIGINS` in `main.py` if the final hostname differs from `cloudsentinel.onrender.com` | Mert | 20 min | **Yes** |
| **T4** | **Live Gemini spike** — provision the billing-disabled key, run `scripts/spike_gemini.py`, paste the measured RPM/RPD, JSON-validity rate and P95 latency into [EVAL_SCORECARD.md](EVAL_SCORECARD.md); leave the deployed demo on the fake provider regardless | Mert | 40 min | No — but it converts "designed for Gemini" into "measured on Gemini" |
| **T5** | **Miro final pass** — Sprint 3 board to Done, burndown exported, new product visuals dropped in, board screenshot into `ProjectManagement/Sprint3Documents/` | Tuana | 45 min | **Yes** — project management is scored separately |
| **T6** | **Sprint 3 review + retrospective** in the README — written after the last standup, with the same honesty as Sprints 1 and 2 (what slipped, what we chose not to do) | Murat + Mert | 40 min | **Yes** |
| **T7** | **3-minute product video** — script is already written in [DEMO_SCRIPT.md](DEMO_SCRIPT.md); record against the deployed URL, one take per room, no live LLM calls on camera | Murat | 90 min | **Yes** |
| **T8** | **Everyone commits** — each member lands at least one reviewed PR this sprint (contributor graph is checked) | Çağla, Tuana | 30 min each | Likely — the rubric rewards visible teamwork |
| **T9** | **Final consistency sweep** — counters (tests/endpoints), no future-dated claims, no dead links, `README.tr.md` in sync, `docs/DEMO_PREFLIGHT.md` walked once on the deployed instance | Mert | 30 min | **Yes** |
| **T10** | **Submission form** — links to repo, live URL, video, Miro board; submitted with hours to spare, not minutes | Murat | 15 min | **Yes** |

**Critical path:** T1 → T3 → T7 → T10. Everything else can run in parallel or
be dropped without failing the submission.

**Deliberately not doing before the gate:** new features, new palettes, the
fraud lane's expansion, Postgres, OIDC. The engineering review was explicit
that polish no longer reduces our risk — see section C of the backlog.

---

## 2 · "Canlı data ve çalışan ürün" — what it actually takes

The honest split: **live data is already possible today; a live *product* is
not a two-day job.** Both halves below are real answers, not excuses.

### 2a · What already works (today, no new code)

Three lanes are built and tested; each is one environment variable.

| Mode | Command | What it proves |
|---|---|---|
| **Self-telemetry** — the app's own request history becomes the cost dataset | `make demo-live` (`SENTINEL_COSTS_SOURCE=self`) | Genuinely live, genuinely accumulating data with no external account: click around, watch the estate grow, `GET /telemetry/usage` is the source |
| **Real billing export** — credential-free | `python scripts/import_costs.py export.csv -o /tmp/costs.json` then `SENTINEL_COSTS_FILE=/tmp/costs.json` | Azure Cost Management / AWS CUR CSV headers are recognized and converted to the dataset contract. **Keep the real export out of git** — point at a path outside the repo |
| **External JSON feed** | `SENTINEL_COSTS_FEED_URL=…` (also `SENTINEL_SECURITY_FEED_URL`, `SENTINEL_FRAUD_FEED_URL`, `SENTINEL_MARKET_FEED_URL`) | Polling ingestion with a TTL cache, malformed rows dropped, and an ordered fallback: fresh fetch → last good payload → bundled fixture |

Two guarantees hold for the three data lanes (costs / security / fraud) in
every mode: `/health` reports each data lane's source **as served, not as
configured** (a dead feed says `mock (feed unavailable)`, and the dashboard
badge follows), and the detectors still refuse to score a lane without
enough real accumulated history — no fabricated days. The market catalogue
reports its own served source (curated / feed / `mock (feed unavailable)`)
on the `/market/opportunities` response and its panel badge; it is not part
of the `/health` data-source manifest.

**If someone on the team can export two weeks of real billing from any cloud
account (a personal one is fine), the live-data story becomes a measured one
in about 20 minutes:** import it, run `scripts/benchmark_detection.py` against
it, and paste the numbers into the scorecard. That is the single highest-value
optional item left, and it is T4's natural companion.

### 2b · What "a working product" still needs (after August 2)

From the July 18 engineering review, in the order that closes real risk. None
of these fit in two days, and pretending otherwise is how a demo dies on
stage.

1. **Authentication that a stranger cannot forge** — local `/auth` with roles
   and server-derived identity ships today; OIDC/SSO and tenant isolation do
   not.
2. **Durable state** — Postgres + migrations. Today an ephemeral restart
   erases decision memory and the audit ledger, which contradicts the
   product's own promise. This is the biggest single gap.
3. **Scheduled ingestion against a real account** — the opt-in watchdog
   already pulses on a cadence; it needs a real source behind it and
   credential handling we do not have.
4. **Pulse as a background job** — `POST /runs` → `202 + run_id` with a worker
   and progress, instead of a long synchronous request.
5. **One real side effect** — approval opens a real Jira ticket or
   GitHub/Terraform PR, the URL lands in the audit record, and a verification
   step re-checks cost afterwards. This is what turns detect-to-database into
   detect-to-resolution.

**The honest label until then**, which we should use in the video rather than
let a juror discover it: *a well-engineered prototype that faithfully
simulates the behaviour of a production product — real detection, real agent
reasoning, real human-in-the-loop governance, simulated execution.*

---

## 3 · Submission checklist (tick on August 2)

- [ ] Live URL responds, `/health` green, `/ready` green
- [ ] `scripts/smoke.sh` passes against the deployed instance
- [ ] Read-only mode confirmed on the public link (a write answers 403 and the
      dashboard says why)
- [ ] Video uploaded, ≤ 3 minutes, ends on the live URL
- [ ] README: Sprint 3 review + retrospective written, counters correct, no
      future-dated claims
- [ ] Miro board Done, burndown exported, screenshots in
      `ProjectManagement/Sprint3Documents/`
- [ ] Every member has at least one merged PR this sprint
- [ ] Submission form sent, with the confirmation screenshotted into the
      evidence pack
