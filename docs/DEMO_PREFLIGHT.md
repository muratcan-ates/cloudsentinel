# Demo Pre-flight — stage, reset, warm-up, recovery

*The operations checklist for the video shoot and the live jury demo.
Companion to [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md) (the 3-minute cut). The
whole stage runs on the deterministic provider — no key, no quota, no
network gamble.*

## T-30 minutes — environment

```bash
git pull                     # be on the submitted HEAD
make test                    # ruff + full suite green before anything else
```

## Raise the stage

```bash
make demo                    # fake provider · dates rebased to this week · reset armed
```

- Open `/` and `/brain`; `?tour=1` replays the six-stop guided walk.
- Masthead must read **SYSTEM ONLINE — … — AI FAKE PROVIDER**.
- Live-data variant: `make demo-live` serves the cost lane from the app's
  own telemetry (`SENTINEL_COSTS_SOURCE=self`) — real traffic, honestly
  badged in the masthead. Pick one stage per take; don't switch mid-scene.

## Between takes — reset

```bash
curl -X POST "http://127.0.0.1:8000/ops/demo-reset?seed=1"
```

Wipes decisions, proposals and the pulse log; **keeps** the AI-spend
ledger and LLM cache. `seed=1` plants six past verdicts so the decision
memory and intelligence panels are never empty on camera.

## Verification sweep (second shell)

```bash
make smoke                   # 13-step PASS/FAIL against the live server
```

## Public link (Render) — warm-up

The free tier sleeps; the first request can take ~46 seconds and a short
timeout reads as "HTTP 000 — the link is dead" when it is not.

```bash
curl --max-time 90 https://<the-live-link>/health
```

- Warm it **10 minutes before** the jury clicks anything.
- Sanity check it is *our* app: `/openapi.json` title must be
  **`CloudSentinel API`** (an old service once squatted the URL).
- The public deploy ships `SENTINEL_READONLY=1` — the masthead shows
  **READ-ONLY DEMO** and writes return 403. Don't plan any approve/reject
  beat on the public link; that beat belongs to the local stage.

## Recovery drills

| Symptom | Move |
|---|---|
| Masthead says **RECONNECTING** | Panels keep the last scan; restart `make demo`, reload once |
| Panels look stale mid-take | `POST /ops/demo-reset?seed=1`, reload, re-enter the scene |
| Database in a weird state | Stop the server, delete `cloudsentinel.db`, start again (schema + seed rebuild on boot) |
| Rehearsing failure on purpose | `make drill` (`scripts/failure_drill.sh`) before the real day |

## Don'ts

- Don't record against live Gemini unless the spike (`scripts/spike_gemini.py`)
  has been run and quota measured — the fake path *is* the reliability story.
- Don't demo the public link cold; warm it first.
- Don't clear the AI-spend ledger for cosmetics — the self-FinOps panel
  reading real accumulated numbers is a feature, not clutter.
