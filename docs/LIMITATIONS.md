# Limitations

What CloudSentinel does **not** do, said plainly.

The README's [Does / Does Not](../README.md#what-it-does--what-it-deliberately-does-not)
table is the short version and describes design boundaries. This document is
the long version, and it goes further: it also names the places where the
build is thinner than the pitch, where a number is an estimate rather than a
measurement, and what we never got round to verifying.

A jury should be able to read this page and find no surprises left in the
demo. Everything here is a deliberate boundary of a competition build or an
honestly-labelled gap — none of it is hidden behind a confident sentence
somewhere else in the docs. Where a limit is enforced in code, the file is
named so the claim can be checked rather than believed.

---

## 1. The data is synthetic

Every lane serves bundled fixtures by default: cost
(`app/data/mock_costs.json`), security (`app/data/mock_security_events.json`)
and fraud. No real cloud bill has ever passed through this system.

Live paths exist and are wired end to end, but each is opt-in behind an
environment gate and none of them was exercised against a production estate:

| Source | Gate | What it was actually tested against |
|---|---|---|
| External JSON feed per lane | `SENTINEL_*_FEED_URL` | hand-written fixtures matching the mock contract |
| Billing CSV import | `scripts/import_costs.py` + `SENTINEL_COSTS_FILE` | synthesized Azure/AWS-shaped exports |
| The app's own request telemetry | `SENTINEL_COSTS_SOURCE=self` | our own traffic — real numbers, but they measure us, not a cloud |
| Simulated live stream | `SENTINEL_SIM_STREAM`, `SENTINEL_COSTS_SOURCE=sim` | a bounded random walk seeded from the fixture's real history |

The simulated stream is labelled **SIMULATED LIVE** on the dashboard badge
and pins `simulated: true` into its own responses — deliberately never the
plain "LIVE DATA" wording, which is reserved for a source that is genuinely
external.

With `SENTINEL_REBASE_DATES=1` (the demo stage) fixture dates are shifted
forward by whole weeks so a jury sees a spike from this week. The shift is
quantized to weeks precisely so weekday alignment — and with it the seasonal
baseline — survives, and every lane gets the same delta so cross-lane
same-day correlations stay intact. The dates on screen are still fixture
dates, moved.

## 2. Execution is simulated

Approving and executing an action **never touches infrastructure**. The
`executed` transition writes a `SIMULATION` marker into the action detail
and the dashboard badges it as such (`app/actions.py`).

What is real on that path:

- the decision record, the operator identity behind it and the append-only
  audit trail;
- the optional outbound webhook (`SENTINEL_EXECUTE_WEBHOOK_URL`), which
  really does POST the incident report to a configured endpoint after the
  commit, with the delivery result recorded on the action.

What is not real: any change to any cloud resource, ever. "Mutation
simulated, dispatch real" is the exact line, and it is the line the
architecture doc and the UI both use.

## 3. Storage is ephemeral

SQLite on the deploy target's disk. A redeploy — or the free tier spinning
the instance down — loses the database. The schema rebuilds itself from
nothing at boot (seed-on-startup) and a cold vitrine files its first cards
within seconds, so the public link is never an empty desk. But nothing
stored here survives as a system of record, and the decision ledger a jury
browses today may not be the one there tomorrow.

Postgres was deliberately not adopted: a free managed instance expires
inside 30 days, which would kill the public link shortly after the
competition — a worse outcome than a resettable one.

## 4. The deployed build runs a fake model

The public deployment runs with `SENTINEL_FAKE_LLM=1`. Every agent answer
there comes from the deterministic fake provider, not from Gemini. The
consequences are visible and should be read as such:

- **agent confidence reads 0.50** on the fake lane — a fixed placeholder,
  not a measured belief. A 0.50 on the demo is the fake provider speaking,
  and because 0.50 sits under the debate threshold it also means the
  escalation ladder is exercised on essentially every card;
- narratives are composed deterministically from the run's real facts, so
  they are honest about the numbers but they are not model prose;
- the review panel's three "reviewers" are three deterministic personas
  with genuinely different charters, not three distinct models.

The live Gemini path is implemented, and was verified end to end against a
real key on a billing-disabled project (schema-parsed responses, three
free-tier models, measured latencies). The demo does not run on it by
choice: zero quota consumed, zero cost, no key anywhere near the recording.

## 5. Detection is statistics, not machine learning

A rolling-baseline z-score with an optional median/MAD detector, optional
day-of-week seasonality and optional leave-one-out scoring. No model is
trained; nothing is learned from history beyond the arithmetic in
`app/detection.py`.

Consequences worth stating:

- a service needs at least **7 records inside the window** to be scored at
  all; below that it is reported in `insufficient_data_services` rather
  than guessed at — two data points are not a baseline;
- a record whose cost is missing, non-numeric or non-finite (NaN, ±∞) is
  **dropped before any statistic is computed** and counted on the run. It
  is not scored, and it is not silently treated as zero;
- the system detects **deviation**, never cause. "Why did this happen" is
  the Analyst's hypothesis with cited evidence, and it is labelled as a
  hypothesis;
- seasonality only engages when every weekday bucket is large enough to be
  a baseline of its own; otherwise the flat baseline is kept rather than
  silently disabling detection.

## 6. The money figures are scenario estimates

`estimated_savings` projects the anomaly's daily excess over 30 days and
applies a capture factor (0.35 cautious / 0.70 bold). That is an
assumption — that the excess persists and that a share of it is contained —
not a forecast and not an accounting figure.

The assumption travels with the number: every surface that shows a saving
also shows the method string that describes it. The figures are computed in
Python and never generated by the model, and narrative money figures are
post-checked within ±5% against the computed ones. None of that makes them
finance-grade.

## 7. One deployment serves one estate

There is no multi-tenancy. No organizations, no projects, no per-tenant data
isolation — every reader of a deployment sees the same estate.

Identity is local: salted PBKDF2 (240k rounds) accounts with viewer /
analyst / approver / admin roles, sessions that expire after 12 hours and a
logout endpoint, and every decision carries a server-derived operator
identity rather than a name from the request body. It is not SSO, not OIDC,
and there is no password reset, no MFA and no account recovery — an
organization's real identity provider is out of scope for this build.

The showcase deployment sidesteps the whole question by running read-only
(`SENTINEL_READONLY=1`): every write is refused with an explanation, and the
UI disables the verbs it cannot honour rather than offering buttons that
403.

## 8. Alert suppression hides repeats on purpose

While a card is still **undecided**, later signals for the same service on
the same lane fold into it as a counted repeat instead of opening their own
card. This is the intended behaviour — it is what keeps a service that
deviates for five days straight from producing five cards — but it means a
reader must look at `suppressed_count` to see how often something recurred.

The scope is deliberately narrow, and the narrowness matters:

- only a `proposed` card suppresses. The moment a human approves, rejects
  or executes, that conversation is closed and the next signal earns its own
  card — folding a new fact into a decided card would quietly apply an old
  verdict to something nobody judged;
- suppression never crosses lanes: a fraud hold and a cost card on the same
  service on the same day are two different conversations;
- the window is 24 hours by default and tunable
  (`SENTINEL_SUPPRESSION_WINDOW_HOURS`); zero disables it entirely.

## 9. The fraud lane is experimental

It exists to show the same governance rails generalize past cost and
security, not as a product line. Scores come from four published rules whose
points are individually attributed, so any score can be recomputed by hand.
It suggests a hold; it never blocks a payment, and no ML is involved.

## 10. Framework references are a lookup table

The MITRE ATT&CK technique on a security signal and the FinOps Framework
capability on a cost card come from a mapping table in `app/enrichment.py`,
keyed by the surface or the card's category. That is the design — the same
input always reads the same way, and nothing is generated — but it also
means the coverage is exactly as wide as the surfaces we modelled. An
unmapped surface falls back to the lane's general entry rather than
inventing a plausible technique id. These are recognizable references, not a
classification engine.

## 11. It is sized for a demo estate

A single process. Request-triggered work rather than a scheduler (the one
exception is an in-process watchdog thread that fires the pulse on an
interval). SQLite in WAL mode with a 5-second busy timeout. Proposal
expiry, feed refresh and cache pruning all happen on the next request
rather than on a clock, because the deploy target sleeps between requests
and no scheduler could run there anyway.

That design is correct for a free-tier deployment and wrong for a fleet.
Postgres, a worker queue, Redis and real provider adapters are named
non-goals of this build, not oversights.

## 12. What we did not verify

The honest end of the list:

- **no load or soak testing.** The suite carries performance budgets on
  individual endpoints; nobody has run this under concurrent load;
- **no external penetration test.** The build has been through repeated
  internal adversarial review and ships with a strict CSP, security
  headers, an SSRF guard on outbound calls, parameterized SQL and a
  read-only showcase mode — but no third party has attacked it;
- **96% line coverage is not 96% of behaviour.** It means most lines
  execute during the suite. Property-based tests (`tests/test_detection_properties.py`)
  cover the detector's invariants against generated hostile input, which is
  a stronger guarantee, and they exist for the detector only;
- **the agent-chain evaluation is a 288-case golden set of our own making**
  (`docs/EVAL_SCORECARD.md`). It is honest about its false positives; it is
  not an independent benchmark;
- **accessibility was assessed manually** against WCAG basics (landmarks,
  labels, contrast, reduced-motion, skip link). No automated audit tool was
  run against the deployed page.

---

*If something in the demo looks like it does more than this page admits,
this page is the one to believe.*
