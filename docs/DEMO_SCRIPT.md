# Demo Script — the 3-minute cut

*Nine beats, 3:00 exactly. One thesis: **the machine watches, the human
decides — and the record can be checked.** The stage runs on the
deterministic provider, so nothing on camera waits for a quota.
Operations, staging and recovery live in
[`DEMO_PREFLIGHT.md`](DEMO_PREFLIGHT.md); this page is what you click and
what you say.*

**Honesty, fixed for every take.** The data is synthetic (competition
rule), execution is **simulated** — no real infrastructure is touched —
and the AI provider is the deterministic fake unless the masthead says
otherwise. Webhook dispatch is the one real side effect and it is not in
this cut. Say "simulation" out loud at least once (beat 4 does it).

---

## Stage it — five minutes before you roll

```bash
make demo                                             # fake provider · dates rebased · reset armed
curl -s -X POST "http://127.0.0.1:8000/ops/demo-reset?seed=1"
curl -s http://127.0.0.1:8000/ops/preflight | python3 -m json.tool | head -5
```

- The last command must print `"ok": true` and `"failures": 0`. Warnings
  are postures, not defects (open writes, standing watch off, no pulse on
  record yet) — see the preflight page.
- Open **`http://127.0.0.1:8000/watch?threshold=4`** — at a bar of 4.00
  the anomaly panel reads **All quiet.**, which is the establishing shot.
- Type your name into the **Operator** box in the colophon. Every verdict
  then reads `approved · <your name>` instead of `operator`.
- Mission dropdown on **finops**, palette on **horizon**, agent feed
  closed, accessibility panel closed and **reset** (it remembers itself in
  this browser between takes).
- Second browser tab, already open and idle, for beat 6.
- Terminal visible for beats 1 and (if needed) 6.
- `?seed=1` plants **six** past verdicts so the memory panels are not
  empty. They are seeded, not decided — never call them operator history.

**Between takes:** `curl -X POST ".../ops/demo-reset?seed=1"`, flip the
mission back to **finops**, reload `/watch?threshold=4`, press **reset**
in the accessibility panel.

**Not on the public Render link.** It ships `SENTINEL_READONLY=1`: Pulse,
the mission switch and every verdict are disabled there. Beats 2, 4 and 7
are local-stage only.

---

## The timing spine — 3:00

| # | Beat | Clock | Length |
|---|---|---|---|
| 1 | Healthy start | 0:00 → 0:18 | 18s |
| 2 | One signal surfaces | 0:18 → 0:40 | 22s |
| 3 | The chain reasons, with citations | 0:40 → 1:08 | 28s |
| 4 | The human decides | 1:08 → 1:30 | 22s |
| 5 | It persisted | 1:30 → 1:45 | 15s |
| 6 | The ledger answers *intact* | 1:45 → 2:03 | 18s |
| 7 | The mission switch changes the posture | 2:03 → 2:22 | 19s |
| 8 | The desk — what it can prove about itself | 2:22 → 2:45 | 23s |
| 9 | Accessibility, and the close | 2:45 → 3:00 | 15s |
| | **Total** | | **3:00** |

---

## Beat 1 · Healthy start — 0:00 → 0:18 (18s)

**Do:** hold on the masthead — `SYSTEM ONLINE — LAST SCAN … — MOCK DATA —
AI FAKE PROVIDER` — then cut to the terminal already showing the
pre-flight output from staging: `"ok": true`, `"failures": 0`.

**Say:** "CloudSentinel is running, and it checks the stage itself: ten
checks, zero failures."

**If this fails on camera:** if the masthead reads **RECONNECTING**, the
panels are holding the last good scan — restart `make demo` and reload
once. If the terminal is empty or the call hangs, read the masthead
instead: online, mock data, fake provider — that is the same claim in
three words.

---

## Beat 2 · One signal surfaces — 0:18 → 0:40 (22s)

**Do:** drag the **Sensitivity** slider from 4.00 down to **2.00** — the
panel fills: `compute`, `1,183.40 vs baseline 197.98`, `6.0× the usual
daily spend`, z **3.61**, critical. Then press **Pulse ▸** on the desk
card. The chip row under **anomalies** re-measures its `reflex … ms` —
that badge is a measurement, not a claim.

**Say:** "I bring the bar down to two — compute steps forward at six
times a normal day — and Pulse runs the whole chain."

**If this fails on camera:** Pulse greyed out means you are on the
read-only link — switch to the local tab. If the run errors, the ledger
prints *Pulse request failed* and the panels keep their last state: press
**Re-scan →** and carry on, the signal is already on screen without the
chain. Do not press Pulse twice; it is idempotent, so the second run
files nothing and the story stalls.

---

## Beat 3 · The chain reasons, with citations — 0:40 → 1:08 (28s)

**Do:** click **investigation** in the top nav — the signals are sorted
by z-score, so compute is already the open one. Click **run analyst
agent →**; the panel becomes *What happened — Analyst agent* (it adds
*· cached* when the Pulse already ran that signal), with the line **cited
evidence E12 · E13 · E14 — rows of the fourteen-day series**. Then open
the fold **review panel convened — consensus** and let the three reviewer
rows sit on screen; one of them is marked **dissent**.

**Say:** "The analyst cites the exact rows it read; its confidence was
low, so three reviewers argued the call — and the one who disagreed stays
on the record."

**If this fails on camera:** the analyst button fires a real request — if
it sticks on *analyst working…*, skip it: the chain from the Pulse is
already on the card, so open **agent chain — N hops, traced** and
**decision memory — N prior verdicts shaped this proposal** instead. If
there is no panel fold, that signal did not escalate — say "this one
agreed, so no panel was needed" and pick the other signal in the rail.
From the watch room, click **investigate →** on the compute signal.
The selected signal opens directly in the Investigation room with its
fourteen-day evidence and agent analysis context.

---

## Beat 4 · The human decides — 1:08 → 1:30 (22s)

**Do:** click **decision desk** in the top nav. On the compute card, type
into the rationale box — *Confirmed with the platform team — the batch
job was unplanned.* — and click **approve →**. The card turns:
`approved`, the little map reads `filed → approved → execute`, and the
status line says **approved · <your name> — ready for simulated
execution**.

**Say:** "Nothing runs by itself — I write my reason, I approve, and even
the execution is a simulation."

**If this fails on camera:** a rejection without a reason is refused by
the server, and the input says so at the box — that refusal is a feature,
say it and type a reason. A **409** prints *Decision already recorded —
guard held* in the ledger: also a feature (the card was decided once),
move to the next card. If the identity line lost your name, the verdict
carries the Operator field — still an accountable name.

---

## Beat 5 · It persisted — 1:30 → 1:45 (15s)

**Do:** reload the page (⌘R). It comes back on the decision desk, the
card is still `approved`, and **section V — decision ledger** has your
verdict at the top: *Approved · compute* with your rationale under it.

**Say:** "I reload — the verdict, the name and the reason come back from
the database, not from the screen."

**If this fails on camera:** if the ledger comes back reading *No
operator decisions recorded yet*, something reset the stage mid-take —
cut. In the second tab, `http://127.0.0.1:8000/decisions` shows the same
row as raw JSON; that is the recovery shot, and it is arguably the
stronger proof.

---

## Beat 6 · The ledger answers *intact* — 1:45 → 2:03 (18s)

**Do:** open **what it can prove ▾** in the top nav and ⌘-click **Ledger
integrity** so it lands in the second tab. The response opens on
`"ok": true`, with `entries` and `verified` equal and the method spelled
out: *the ledger is not asserted intact, it is recomputed*.

**Say:** "Every decision is sealed with the hash of the one before it, so
the trail is not a promise — the server re-computes it, and it answers:
ok, true."

**If this fails on camera:** if the JSON is unreadable at video size, run
`curl -s http://127.0.0.1:8000/audit/verify | python3 -m json.tool | head -5`
in the terminal instead — same answer, five lines. If it ever answers
`"ok": false`, read `first_break` aloud: it names the entry, the source
row and which of the four ways it broke. That is the endpoint doing its
job, not a failed demo. Come back to the app tab before beat 7.

---

## Beat 7 · The mission switch changes the posture — 2:03 → 2:22 (19s)

**Do:** back on the app tab, click **watch**, then set the **Mission**
dropdown to **security**. The switch rides a Pulse: the same two days
come back re-scored — compute `265.09`, database `108.83` instead of 3.61
and 3.60 — because the security mission scores with MAD over a
fourteen-day baseline, where finops uses z-score over twenty-eight.

**Say:** "Same engine, another mission file — security scores with MAD on
a fourteen-day window, so the same two days come back with different
numbers."

**If this fails on camera:** if nothing moves, you are on the read-only
link (the switch rides a write) or the last Pulse is still running — wait
one beat and re-select. Keep the sensitivity slider where it is and do
not discuss it: the slider still governs the list's own bar, while the
mission's 1.75 governed the Pulse. If pressed by the jury, that is the
honest answer — the operator's bar outranks the mission's. Terminal
fallback: `curl -s "http://127.0.0.1:8000/anomalies"` shows `mission`,
`detector` and `window_days` in the response.

---

## Beat 8 · The desk — what it can prove about itself — 2:22 → 2:45 (23s)

**Do:** stay on the watch room and scroll to the top card, **the desk**.
Point at the left column, **what it can prove**: eight rows — Ledger
integrity, Decision quality, Run receipts, Runbook hit rate, Watch
vitals, Pre-flight, Detector backtest, Self telemetry — each showing a
value read from the endpoint it names.

**Say:** "This is not a feature list — every row here is read from a live
endpoint, so the product answers for itself."

**If this fails on camera:** these rows are read when the page loads —
your beat 5 reload is what makes them current, so if a value looks stale,
say so rather than claiming it is live. A row that cannot answer prints
**unavailable** by design; that is honest and you can say it in one
sentence. Worst case, the same twelve links sit in **what it can prove ▾**
in the nav.

---

## Beat 9 · Accessibility, and the close — 2:45 → 3:00 (15s)

**Do:** click the **☀** button at the bottom right. In the panel, turn
**High contrast** on and press **Text size +** twice — the page changes
under it while you speak. Leave it open on the last frame.

**Say — the closing line, 15 seconds:** "One last thing: the
accessibility panel is ours — no outside script, no call to another host,
nothing leaves the browser. Contrast, text size, motion: all local. That
is CloudSentinel — the machine watches, the human decides, and the record
can be checked."

**If this fails on camera:** if the panel does not open on the click, Tab
to the ☀ button and press Enter (Escape closes it). If a toggle does
nothing visible, press **reset** and use **Text size +** only — the size
step is the most visible change on video. Remember to press **reset**
before the next take: the settings persist in this browser.

---

## Reading notes for the presenter

- **MAD** is said as one word, *mad* — the screen carries the meaning, so
  do not try to fit "median absolute deviation" into beat 7.
- The five numbers you say are the only ones you say, and all five are on
  the screen behind you: *ten checks* (1), *two* and *six times* (2),
  *three reviewers* (3), *fourteen-day window* (7). If a figure is not on
  screen, it does not get said.
- The words that carry the product are short: **watch**, **decide**,
  **prove**, **simulated**. Lean on them; skip the adjectives.
- Every sentence above is one breath. If you stumble, stop and re-take
  the beat — the cuts are per beat by design.
- Never say "detects fraud automatically", "fixes the problem" or
  "production". Say **suggests**, **simulated**, **prototype**.

---

## After the video — extra beats for the live jury demo

These are too slow for 3:00 and land well when someone asks:

- **The chain, tamper-checked:** edit a rationale straight in
  `cloudsentinel.db` with `sqlite3`, then `GET /audit/verify` — it names
  the entry, the row and `source_modified`.
- **Receipts:** `GET /analytics/receipts` — agent turns, measured
  milliseconds and model calls for every watch cycle, assembled on read.
- **Decision quality:** `GET /analytics/quality` — acceptance rate,
  recurrence, calls per decision, and the uncertainty sources the agents
  themselves reported.
- **Reflex drafts:** `GET /reflex/suggestions` — "you approved this
  signature every time; here is the rule I would write" — drafts only,
  with no code path that can adopt one.
- **Runbooks:** search *ec2 cost spike* in the brain room, then
  `GET /runbooks/effectiveness` for whether those playbooks were any
  good.
- **The backtest:** the brain room's chart — every scorer in the registry
  against planted ground truth, moving with the sensitivity slider.
- **Smoke sweep:** `make smoke` in a second shell against the live server.
- **The whole trail, portable:** `GET /decisions/export` as CSV.
