# Demo Scenario — the incident is loaded while the camera rolls

*Seven beats for the live jury demo (plus an optional 7b, and an optional
security lane at the end). The thesis of this page is narrower
than [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md)'s three-minute cut: **the chain runs
on data supplied at that moment, not on a pre-baked card.** Every beat below
was run against the app and the figures quoted are the ones it actually
answered with — not the ones it ought to.*

## Why a generator instead of a fixture

The fair question about any AI demo is "was that card already in the
database?". So the demo never opens on the incident. It opens on a **healthy
estate**, and then `scripts/demo_incident.py` writes the incident into the
dataset contract the product already reads — the credential-free
`SENTINEL_COSTS_FILE` lane that `scripts/import_costs.py` feeds a real
billing export through (`app/feeds.py::read_costs_file`). Nothing downstream
is special-cased: the detector scores those numbers, the analyst triages that
event, and `app/recommender.py::estimated_savings` computes the money from
that baseline and that spike.

**The incident.** `payment-api` (AWS) holds ~$420/day for four weeks and
bills **$693 on the newest day (+65%)**. On that same day the security lane
sees a burst of public storage-object reads against the same service — one
root cause (a bucket left publicly readable), two lanes, and the operator
gets to join them.

---

## Before you start

```bash
make test                       # ruff + 1190 tests green before anything else
export DEMO=/tmp/cloudsentinel-demo
export SENTINEL_FAKE_LLM=1 SENTINEL_DEMO_RESET=1
export SENTINEL_COSTS_FILE=$DEMO/incident_costs.json
```

The security lane is a **second terminal**, and it is optional — see
[the security lane](#the-security-lane-second-terminal-optional) below. Skip
it entirely if you are short on time; beats 1–7 all stand without it.

Ports below are `8000` for the stage and `8001` for the read-only mirror in
beat 7. Any pair works.

> The generator writes **outside the repository** (default: your temp dir;
> `-o` above pins it to `/tmp/cloudsentinel-demo`). Nothing it produces is
> ever committed, and `git status` stays clean through the whole demo.

---

## Beat 1 · A healthy start

**Do**

```bash
.venv/bin/python scripts/demo_incident.py --healthy -o $DEMO/incident_costs.json
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000     # leave running
```

then, in the second terminal:

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/anomalies | python3 -m json.tool | head -8
```

or on the dashboard: open `/`, press **Re-scan**.

**Shows** — `/health` answers
`"data_sources":{"costs":"file",…}` (the badge says *file*, not *mock*), and

```
records_analyzed 140 | anomaly_count 0
inbox count 0
```

Four weeks, five services, 140 rows, **nothing flagged**, decision inbox
empty.

**Say** — "This is a quiet estate: four weeks of real daily spend across five
AWS services, and the detector has nothing to say about any of it. Remember
that number — zero."

**If it fails** — if `/health` says `costs: "mock (file unavailable)"`, the
path in `SENTINEL_COSTS_FILE` is wrong or the file was not written. The
unmistakable tell is on the anomaly list: the bundled fixture flags

```
compute    2026-06-29  cost 1183.40  z 3.61  critical
database   2026-07-02  cost  246.30  z 3.60  critical
```

`compute` / `database` / `network` / `storage` are the *fixture's* service
names — if you see those instead of your five (`payment-api`, `checkout-web`,
`ledger-db`, `object-store`, `edge-cdn`), you are not on your own data.
Re-run the generator and check `echo $SENTINEL_COSTS_FILE`. No restart is
needed — the file is re-read on every request.

---

## Beat 2 · Load the incident — live, no restart

**Do** (leave the server running)

```bash
.venv/bin/python scripts/demo_incident.py -o $DEMO/incident_costs.json
```

**Shows** — the generator prints what it wrote, loudly enough to read aloud:

```
  THE INCIDENT — payment-api (AWS)
    baseline, 27 prior days   $420.03/day
    newest day 2026-08-01      $693.00
    rise                          +65.0%
    last five days   07-28 $415.38, 07-29 $422.52, 07-30 $412.02,
                     07-31 $426.30, 08-01 $693.00

  WHAT THE DETECTOR WILL SAY  (threshold 2.0 · detector zscore)
    payment-api  2026-08-01  $693.00 vs baseline $429.78  z=5.16  critical

  WHAT THE RECOMMENDER WILL COMPUTE FROM THOSE NUMBERS
    daily excess      $263.22
    cautious / month  $2,763.81
    bold / month      $5,527.62
```

It also prints a SHA-256 of the file, so "same input, same output" is
checkable rather than asserted.

**Say** — "I am writing a new day of billing data right now, while you watch.
$693 against a $420 baseline. The app has not been restarted."

**If it fails** — the preview is computed by importing the product's *own*
`run_detection`, so it cannot drift from the dashboard. If the import is
unavailable it degrades to one line (`detector preview unavailable …`) and
the file is still written correctly — carry on to beat 3, the app is the
authority anyway. If the script exits non-zero it is telling you the estate
grew a second, unplanned anomaly; try `--seed 42`.

---

## Beat 3 · The agent's finding

**Do**

```bash
curl -s -X POST http://127.0.0.1:8000/pulse | python3 -m json.tool
```

or on the dashboard: press **Pulse**.

**Shows** — the whole chain, on the numbers from beat 2:

```
signals 1 · security 1 · fraud 3 · holds 2 · filed 1 · reflex_ms 0.5
headline:  1 cost + 1 security + 3 fraud signals — 1 new proposal await the operator
engineer:  cost 1 / security 1 / fraud 3 · analyzed 1 · filed 1, reused 0 ·
           cross-lane 2 · strongest |z| 5.16 on payment-api
chain:     payment-api · critical · triage REAL · action 1 · proposed · CAUTIOUS
```

and the card the operator has to answer:

```
1 | Review payment-api capacity during the next low-traffic window
    analyst: payment-api recorded 693.00 on 2026-08-01 against a 429.78
             baseline — z +5.16, well clear of its recent range.
    savings: daily_excess 263.22 · cautious 2763.81 · bold 5527.62
```

`reflex_ms` is a live timing and will differ on your machine — it is the only
number on this page that does.

**Say** — "The detector scored it at z 5.16 against its own rolling baseline,
the analyst triaged it as real and quoted the two figures back, and the
recommender computed $263 a day of excess. Those are Python's numbers — the
model writes the sentence, the code writes the money."

**If it fails** — an empty `chain` means beat 2's file was not picked up; see
beat 1's fallback. A `budget_exhausted: true` is harmless (the agents fall
back to rule-based text and the figures are unchanged). `POST /pulse` is
idempotent, so pressing Pulse twice costs nothing and files nothing new.

> **Expect three cards, not one.** Cards #2 and #3 are fraud holds from the
> bundled fraud fixture — that lane has no file input, so it stays on the
> mock and files its own two cross-lane cards. Say it before anyone asks:
> "three lanes, one inbox." The cost card is #1, the one named `payment-api`.

---

## Beat 4 · The human decides, with a reason

**Do**

```bash
curl -s -X POST http://127.0.0.1:8000/actions/1/approve \
  -H 'Content-Type: application/json' \
  -d '{"rationale":"Cost and access spiked on payment-api the same day; approving the cautious capacity review while we close the public bucket."}'
```

or on the dashboard: type the rationale into card #1 and approve.

**Shows** — `HTTP 200`, and the card comes back with its trail:

```
state approved | decided_by operator | decided_at 2026-08-01 23:31:32
  filed     by agent:recommender
  approved  by operator — "Cost and access spiked on payment-api the same day; …"
```

**Say** — "Nothing executes itself. A person approves, and the reason goes on
the record with the identity — that rationale is what the system reads back
as decision memory next time payment-api misbehaves."

**If it fails** — `422 {"detail":"a rejection must carry a rationale — say
why the hand said no"}` means you hit `/reject` without a reason; that is the
product refusing an unexplained "no", and it is worth showing on purpose. If
you are signed in, `decided_by` carries your username instead of `operator` —
the server derives it, the request body cannot forge it.

---

## Beat 5 · Refresh — the decision is durable

**Do** — reload the dashboard. Better, kill the server with `Ctrl-C` and
start it again, then:

```bash
curl -s http://127.0.0.1:8000/actions | python3 -m json.tool | head -20
curl -s http://127.0.0.1:8000/pulse/last | python3 -m json.tool | head -5
```

**Shows** — after a full process restart:

```
1 approved operator | "Cost and access spiked on payment-api the same day…"
2 proposed  —
3 proposed  —
/pulse/last ran_at 2026-08-01 23:31:21 | signals 1
```

**Say** — "The verdict survived the process, not just the page. The run
itself is replayable too — a colleague opening this later gets the same
chain and the same briefing."

**If it fails** — if the inbox comes back empty after a restart you are on a
different database; `SENTINEL_DB_PATH` must be the same value in both
terminals (unset is fine — just be consistent). Persistence is SQLite on
disk; Postgres is deliberately out of scope and is written down as such in
[`LIMITATIONS.md`](LIMITATIONS.md).

---

## Beat 6 · A second decision is refused — 409

**Do**

```bash
curl -i -s -X POST http://127.0.0.1:8000/actions/1/approve \
  -H 'Content-Type: application/json' -d '{"rationale":"changed my mind"}'
```

**Shows** — verbatim, from the running app:

```
HTTP/1.1 409 Conflict
{"detail":"action 1 is already 'approved'; only 'proposed' actions can be decided"}
```

`POST /actions/1/reject` answers with exactly the same 409 — the guard is on
the card's state, not on which verdict you send.

**Say** — "A decided card cannot be decided twice. Not the button greying
out — the API itself refuses, with the state it is already in. The audit
trail has one verdict per card, by construction."

**If it fails** — a `404` means you used the wrong id; the cost card is the
one whose title names `payment-api` (id 1 on a fresh stage). A `200` would
mean the card was still `proposed` — you are on a reset database and beat 4
did not land; re-run beat 4 first.

---

## Beat 7 · Read-only refuses at the API, not at the button

Start a **second** server that mirrors the same database in read-only mode —
this is the posture a public demo link would run in. Leave the stage on 8000
running.

**Do**

```bash
SENTINEL_READONLY=1 SENTINEL_FAKE_LLM=1 \
  .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8001
```

then, against the read-only one:

```bash
curl -s  http://127.0.0.1:8001/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/actions
curl -i -s -X POST http://127.0.0.1:8001/actions/2/approve \
  -H 'Content-Type: application/json' -d '{"rationale":"probe"}'
```

**Shows**

```
{"…","readonly":true,"data_sources":{"costs":"file",…}}
200                                     <- reads still work
HTTP/1.1 403 Forbidden
{"detail": "read-only demo mode — write operations are disabled"}
```

Card #2 is still `proposed`, so this is a request the app would otherwise
have accepted — the 403 is the posture, not the card's state. The stage on
8000 stays writable throughout.

**Say** — "On a public link every write is refused at the middleware, before
any handler runs. I'm not clicking a disabled button — I'm posting straight
to the API and it still says no. Reads keep working, so the panels stay
alive."

**If it fails** — if you get a `409` instead, card #2 was already decided;
pick any card still in `proposed`. If port 8001 refuses to bind, pick
another. **Do not set `SENTINEL_READONLY=1` on the stage itself** — see the
first honest note below.

### Beat 7b · The same refusal by *role*, if you have 40 seconds more

`SENTINEL_READONLY=1` is a blunt instrument — it refuses everyone. The
role-based refusal is the one a jury remembers, because the reads still work
*and* the identity is what decides. Start the stage with:

```bash
SENTINEL_REQUIRE_APPROVER=1 SENTINEL_ADMIN_USER=demo-admin \
SENTINEL_ADMIN_PASSWORD=<pick one> SENTINEL_FAKE_LLM=1 \
  .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

Self-registration always creates a **viewer** (`app/auth.py` — the requested
role is validated for a clean error but ignored, so a stranger cannot
register themselves an approver). Three probes, three different answers —
verbatim from the running app:

```
# no session at all
HTTP/1.1 401 Unauthorized
{"detail":"operator mode: deciding requires a signed-in approver or admin session"}

# signed in as the self-registered viewer
HTTP/1.1 403 Forbidden
{"detail":"operator mode: the 'viewer' role cannot decide — the approver or admin role is required"}

# signed in as the bootstrap admin
HTTP/1.1 200 OK        -> state approved, decided_by demo-admin
```

**Say** — "Anyone can read this. Nobody can decide without an approver
session, and the server writes down *which* identity decided — the request
body cannot forge it."

**If it fails** — if the admin login 401s, the account already existed with a
different password: `app/auth.py` never rewrites a stored password on boot,
by design. Use a fresh `SENTINEL_DB_PATH`. If you are short on time, drop 7b
entirely — beat 7 already makes the point.

---

## The proof run: change one number, watch everything move

Run beats 2 and 3 again with a different spike and nothing else changed:

```bash
.venv/bin/python scripts/demo_incident.py --spike 900 -o $DEMO/incident_costs.json
curl -s -X POST 'http://127.0.0.1:8000/ops/demo-reset?seed=1' >/dev/null   # NOT optional
curl -s -X POST http://127.0.0.1:8000/pulse >/dev/null
curl -s http://127.0.0.1:8000/anomalies | python3 -m json.tool
```

> ### The reset is mandatory, and this is the one way this demo bites back
>
> **Skip the reset and the money figures will not move.** Measured, on the
> stage: after `--spike 900`, `GET /anomalies` correctly reports the new
> `cost 900.0 / baseline 437.17 / z 5.18`, and the analyst re-runs and quotes
> the new numbers — but `POST /anomalies/1/recommend` returns
>
> ```
> action_id 1 | state approved
> savings: daily_excess 263.22 · cautious 2763.81 · bold 5527.62   <- the OLD numbers
> ```
>
> because the event already has a **decided** action attached and the
> recommender hands back the stored proposal instead of filing a new one.
> That is correct product behaviour — a decided card is immutable, which is
> the very thing beat 6 demonstrates — but on camera it looks exactly like
> the hard-coded output you are trying to disprove. Reset first, every time.
> After the reset the same call returns `daily_excess 462.83 · cautious
> 4859.71 · bold 9719.43`. Both numbers in this note were read off the
> running app.

Both runs below were executed against the app; these are its answers.

| | `--spike 693` (default) | `--spike 900` |
|---|---|---|
| cost on the newest day | `693.00` | `900.00` |
| rolling baseline | `429.78` | `437.17` |
| z-score | `5.16` | `5.18` |
| severity | `critical` | `critical` |
| daily excess | `$263.22` | `$462.83` |
| cautious / month | `$2,763.81` | `$4,859.71` |
| bold / month | `$5,527.62` | `$9,719.43` |
| analyst's sentence | "payment-api recorded 693.00 … against a 429.78 baseline — z +5.16" | "payment-api recorded 900.00 … against a 437.17 baseline — z +5.18" |

**Say** — "One number changed in the input file. The baseline moved, the
excess nearly doubled, the monthly projection nearly doubled, and the
analyst's own sentence quotes the new figures. Nothing here is a constant."

**Why the z-score barely moves — say this before the jury asks.** With a
28-day window and a single outlier, the largest z-score that is *arithmetically
reachable* is `sqrt(27) = 5.196`, because the flagged day is inside the
baseline it is measured against and drags it along. 5.16 and 5.18 are that
ceiling. It is not a hard-coded number — it is the detector being honest
about a contaminated baseline, and the card's own trace names it
(`contaminated_baseline — the flagged day is included in the baseline it is
measured against`).

If you want the z-score to move visibly on camera, restart the stage with
the leave-one-out detector, which scores each day against a baseline that
excludes it:

```bash
SENTINEL_LEAVE_ONE_OUT=1 .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

Measured through `GET /anomalies` on the same two files: **z 43.74** at
`--spike 693` and **z 76.91** at `--spike 900`, with the baseline sitting at
the uncontaminated `420.03` and the detector label reading `zscore+loo`.

---

## The security lane (second terminal, optional)

**The security lane has no file input.** The cost lane accepts a file
(`SENTINEL_COSTS_FILE`); security accepts only a feed URL
(`SENTINEL_SECURITY_FEED_URL`, `app/security.py`). So the generated security
payload has to be served over HTTP. It is stdlib only — no new dependency,
no network:

```bash
cd $DEMO && python3 -m http.server 8765          # leave running
```

and on the stage, before starting it:

```bash
export SENTINEL_ALLOW_PRIVATE_TARGETS=1 SENTINEL_FEED_TTL_SECONDS=0
export SENTINEL_SECURITY_FEED_URL=http://127.0.0.1:8765/incident_security.json
```

`SENTINEL_ALLOW_PRIVATE_TARGETS=1` is required because the outbound guard
(`app/netguard.py`) otherwise refuses a loopback target — that refusal is an
SSRF protection working as designed, and the flag is its documented developer
escape hatch. `SENTINEL_FEED_TTL_SECONDS=0` disables the 5-minute feed cache
so beat 2 lands immediately.

**Shows** — `/health` reports `"security":"feed"`, and after beat 2:

```
security signals 1
  payment-api 2026-08-01  47 reads  vs baseline 4.5  z=5.17  critical
```

**Say** — "Same day, same service, second lane: public reads of storage
objects went from three a day to forty-seven. The spend and the exposure have
one cause, and the operator sees both before deciding."

**If it fails** — `/health` will say `"security":"mock (feed unavailable)"`
and the security panel will show the bundled `auth-gateway` login storm
instead. That is the honest fallback working, and it costs you nothing:
**drop the security lane and carry on with beats 1–7**, which never depend
on it.

---

## Honest notes — read these before you are asked

1. **`SENTINEL_READONLY=1` blocks `POST /pulse` too.** The guard is a
   blanket middleware over every `POST`/`PUT`/`PATCH`/`DELETE`, so a
   read-only stage cannot run the chain at all. That is why beat 7 uses a
   *second* server rather than flipping the stage. Verified: `POST /pulse`
   on the read-only port answers `403` with the same body.
2. **The ATT&CK tag on the security signal is wrong for this scenario.** The
   card renders `T1110 Brute Force — Credential Access`. The correct
   technique for public storage reads is `T1530 Data from Cloud Storage
   Object`. The mapping table in `app/enrichment.py` is keyed by service name
   and only knows `auth-gateway`, `api-edge` and `admin-portal`, so
   `payment-api` falls to the default. Nothing here fabricates it — it is a
   lookup table with a gap. **If the tag is on screen, say so**: "our
   framework mapping only covers the three login surfaces today; this one
   should read T1530." Do not claim it is right.
3. **The fraud lane stays on the bundled fixture** and files two cross-lane
   hold cards on every pulse (see beat 3). It has no file input either.
4. **Everything is synthetic and labelled as such.** The dataset's own
   `description` field says so, `/health` says `provider: fake`, and
   execution is simulated — the card carries a `SIMULATION` label and no
   cloud resource is touched.
5. **The money is a scenario estimate, not a forecast.** The card states its
   own method: `(cost - service baseline) x 30 days x capture factor (0.35
   cautious / 0.7 bold)`, assuming the excess persists.

---

## Between takes

```bash
curl -s -X POST 'http://127.0.0.1:8000/ops/demo-reset?seed=1'   # needs SENTINEL_DEMO_RESET=1
.venv/bin/python scripts/demo_incident.py --healthy -o $DEMO/incident_costs.json
```

That clears the decision state (seeding a little synthetic verdict history so
the memory panels are not blank) and puts the estate back to quiet, ready for
beat 1. `GET /ops/preflight` re-checks the whole stage in one call and names
the dataset it is actually serving:

```
dataset  pass  140 cost rows over 2026-07-05→2026-08-01, 5 services
                (checkout-web, edge-cdn, ledger-db, object-store, payment-api)
data_sources  pass  costs=file, fraud=mock, security=feed
```

If that line does not name **your** five services, you are not on your own
data — fix it before rolling.

---

## Reproducibility

The generator is seeded and clock-free in its numbers: same `--seed`
(default 60) and same `--spike` always write byte-identical costs, confirmed
by re-running and diffing. Only the **dates** follow the calendar — the
newest day defaults to yesterday so the jury sees this week — and every
figure in the tables above is date-independent, so they hold whichever day
you present. Pin the dates too with `--end-date 2026-08-01` if you want the
SHA-256 to match as well.

| run | `sha256[:16]` of the cost file |
|---|---|
| `--healthy --end-date 2026-08-01` | `36ff827a4a67875c` |
| `--end-date 2026-08-01` (spike 693) | `6c76c869867b81de` |
| `--spike 900 --end-date 2026-08-01` | `db3d31bed4632373` |

**The estate stays quiet by construction, not by luck with one seed.** The
jitter palette is rotated per service rather than redrawn, which preserves
its sum-zero and bounded-range properties, so the largest \|z\| a quiet
service can reach is **1.686** across all twelve rotations — under the 2.0
threshold with room to spare. Checked exhaustively: over seeds 0–199, every
seed produces *exactly one* cost anomaly (`payment-api`) and *exactly one*
security signal (`payment-api`), and `--healthy` produces zero anomalies for
every one of those seeds. If a seed ever did grow a second anomaly the
generator would exit non-zero and say so rather than let you find out on
camera.

---

## What on this page was actually executed

Every figure above was read off a running instance on 2026-08-02, not
predicted. Specifically verified end to end:

- `/health` reporting `costs: "file"` and `security: "feed"` — the generated
  data really is what the app is serving.
- Beat 1 (140 rows / 0 anomalies / empty inbox), beat 2's generator preview,
  beat 3's `POST /pulse` (headline, engineer line and chain quoted verbatim),
  beat 4's `200` + audit trail, beat 5's durability across a **real process
  restart** (`kill` then restart: card 1 still `approved` with its rationale).
- Beat 6's **409** and beat 7's **403**, pasted verbatim from the wire,
  including the role-based `401`/`403`/`200` trio in 7b and the read-only
  `403` on `POST /pulse`.
- The `422` on an unexplained rejection.
- Both proof runs (`693` and `900`) through `GET /anomalies`,
  `POST /anomalies/1/analyze` and `POST /anomalies/1/recommend`, plus the
  leave-one-out figures (`z 43.74` / `z 76.91`, baseline `420.03`, detector
  label `zscore+loo`).
- `GET /ops/preflight` and `POST /ops/demo-reset?seed=1` (which seeds 6
  decisions).

Two things on this page are *descriptions of code*, not measurements: the
`sqrt(27) = 5.196` z-score ceiling (arithmetic, and it matches the observed
5.16/5.18), and the claim that `T1530` is the correct ATT&CK technique for
public storage reads — that one is a judgement about MITRE's catalogue, and
the only *measured* part is that the card really does render `T1110`.
