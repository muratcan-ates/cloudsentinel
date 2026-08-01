"""Simulated live tape — the market feel, honestly labeled (Sprint 3).

A synthetic random-walk stream: per-service run-rates that tick every few
seconds and occasionally spike, so the demo breathes like a trading floor.
Everything here is generated in process — no billing data, no credentials,
no external source — and every payload says so (``simulated: true``, plus a
plain-language note). Env-gated behind ``SENTINEL_SIM_STREAM=1`` and fully
read-only: state lives in memory, advances lazily on read, and never touches
the database, so the endpoint is safe on the read-only vitrine too.
"""

import os
import random
import statistics
import time
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException

from app.detection import load_mock_dataset, shift_iso
from app.models import StreamReport, StreamService

router = APIRouter(prefix="/stream", tags=["stream"])

SIM_STREAM_ENV = "SENTINEL_SIM_STREAM"

TICK_SECONDS = 2.5
TREND_LENGTH = 48  # sparkline window the dashboard draws
MAX_CATCHUP_TICKS = 240  # an idle server fast-forwards at most this many
SPIKE_TICKS = (6, 14)
WALK_SIGMA = 0.012  # per-tick drift; small keeps the tape calm, not jittery
SPIKE_DRIFT = 0.08
REVERSION = 0.05  # pull toward base — calm lanes stay calm, spikes stand out
BAND = (0.45, 2.6)  # the walk stays within base × band — no runaways
SPIKE_START_CHANCE = 0.0025  # per service per tick — rare enough that calm is the norm
# How loud a full-band excursion (rate at BAND[1] x base) should read to the
# detector. The walk is mapped onto each service's OWN historical spread, so
# a calm day scores near zero and a doubling lands around z 4 — the same
# believable range the rest of the product narrates. Without this mapping the
# quiet lanes (whose fixture history is nearly flat) flagged ordinary days.
FULL_BAND_Z = 6.0

_rng = random.Random()
_lanes: dict[str, dict] = {}
_last_tick: float | None = None


def sim_stream_enabled() -> bool:
    return os.environ.get(SIM_STREAM_ENV, "").strip() == "1"


def reset() -> None:
    """Drop all generator state — tests start each case on a fresh tape."""
    global _last_tick
    _lanes.clear()
    _last_tick = None


def _seed() -> None:
    """Derive one lane per estate service; base rate = mean daily cost / 24.

    The tape mirrors the services the rest of the product talks about, so a
    spike on the tape and a card in the inbox name the same estate. A short
    pre-roll fills the sparkline before the first paint.
    """
    if _lanes:
        return
    by_service: dict[str, list[float]] = {}
    for record in load_mock_dataset()["daily_costs"]:
        by_service.setdefault(record["service"], []).append(record["cost"])
    for service, costs in sorted(by_service.items()):
        mean = sum(costs) / len(costs)
        base = max(mean / 24.0, 0.01)
        # the service's own day-to-day spread, robust to its planted spike:
        # the median absolute deviation, scaled to a standard deviation
        median = statistics.median(costs)
        mad = statistics.median([abs(cost - median) for cost in costs])
        _lanes[service] = {
            "base": base,
            "rate": base,
            "prev": base,
            "trend": [round(base, 2)],
            "spike": 0,
            "daily_mean": mean,
            "daily_spread": max(mad * 1.4826, mean * 0.01),
        }
    for _ in range(TREND_LENGTH):
        _tick_once()


def _tick_once() -> None:
    for lane in _lanes.values():
        lane["prev"] = lane["rate"]
        drift = _rng.gauss(0.0, WALK_SIGMA) + REVERSION * (
            lane["base"] - lane["rate"]
        ) / lane["base"]
        if lane["spike"] > 0:
            drift += SPIKE_DRIFT
            lane["spike"] -= 1
        elif _rng.random() < SPIKE_START_CHANCE:  # noqa: S311 — demo tape, not crypto
            lane["spike"] = _rng.randint(*SPIKE_TICKS)
        low, high = lane["base"] * BAND[0], lane["base"] * BAND[1]
        lane["rate"] = min(max(lane["rate"] * (1.0 + drift), low), high)
        lane["trend"].append(round(lane["rate"], 2))
        del lane["trend"][:-TREND_LENGTH]


def _advance(now: float) -> None:
    global _last_tick
    if _last_tick is None:
        _last_tick = now
        return
    ticks = int((now - _last_tick) / TICK_SECONDS)
    if ticks <= 0:
        return
    _last_tick += ticks * TICK_SECONDS
    for _ in range(min(ticks, MAX_CATCHUP_TICKS)):
        _tick_once()


def _projected_today(lane: dict) -> float:
    """Today's figure for one service, mapped onto its own historical scale.

    The walk lives in multiples of base; the estate lives in dollars with a
    per-service spread. Projecting the raw multiple ignored that spread, so a
    lane whose fixture history is nearly flat flagged an ordinary day while a
    lane carrying a planted spike could never flag at all. Mapping the walk's
    position in its band onto the service's own spread makes every lane read
    on the same scale: at base the day is unremarkable, at the top of the
    band it is FULL_BAND_Z standard deviations out.
    """
    excursion = (lane["rate"] / lane["base"] - 1.0) / (BAND[1] - 1.0)
    projected = lane["daily_mean"] + excursion * FULL_BAND_Z * lane["daily_spread"]
    return round(max(projected, 0.0), 2)


def sim_dataset() -> dict:
    """The curated estate's own history, brought up to today, with TODAY
    projected live from the simulated run-rates (rate × 24 h).

    Inventing a synthetic history was the wrong instinct: it threw away the
    fixture's planted spikes and its real day-to-day variance, so ordinary
    days scored as anomalies (measured: a calm lane crossed the threshold up
    to 30% of the time) while a genuine spike scored an implausible z ≈ 28.
    Keeping the fixture fixes both — the baselines stay the ones the whole
    product narrates, and only today's point moves.

    The history is shifted in WHOLE WEEKS so weekday alignment, and with it
    the seasonal baseline, survives; today is then appended from the walk.
    """
    _seed()
    _advance(time.monotonic())
    today = date.today()
    records = [dict(record) for record in load_mock_dataset()["daily_costs"]]
    newest = max(date.fromisoformat(record["date"]) for record in records)
    yesterday = today - timedelta(days=1)
    if newest < yesterday:
        shift = timedelta(days=((yesterday - newest).days // 7) * 7)
        for record in records:
            record["date"] = shift_iso(record["date"], shift)
    # today belongs to the tape alone — drop anything the shift left on or
    # past it so the live point is never shadowed by a fixture row
    records = [record for record in records if record["date"] < today.isoformat()]
    for name, lane in sorted(_lanes.items()):
        records.append(
            {
                "date": today.isoformat(),
                "service": name,
                "cost": _projected_today(lane),
            }
        )
    dates = [record["date"] for record in records]
    return {
        "description": (
            "simulated live lane — the demo estate's own history with today "
            "projected from the simulated run-rate; no real billing data"
        ),
        "currency": "USD",
        "period": {"start": min(dates), "end": max(dates)},
        "daily_costs": records,
    }


@router.get(
    "/metrics",
    responses={
        404: {"description": "The simulated stream is not enabled here."}
    },
)
def stream_metrics() -> StreamReport:
    """One frame of the simulated tape (env-gated, read-only).

    Answers 404 when ``SENTINEL_SIM_STREAM`` is off — the dashboard hides
    the strip entirely in that case, so a deployment without the flag never
    even hints at a ticker.
    """
    if not sim_stream_enabled():
        raise HTTPException(
            status_code=404,
            detail="simulated stream is not enabled on this deployment",
        )
    _seed()
    _advance(time.monotonic())
    services = [
        StreamService(
            service=name,
            rate=round(lane["rate"], 2),
            delta_pct=round(
                0.0
                if lane["prev"] == 0
                else (lane["rate"] - lane["prev"]) / lane["prev"] * 100.0,
                2,
            ),
            trend=list(lane["trend"]),
            spiking=lane["spike"] > 0,
        )
        for name, lane in _lanes.items()
    ]
    return StreamReport(
        simulated=True,
        note=(
            "synthetic stream — generated in-process for the demo; no real "
            "billing data, credentials or external source involved"
        ),
        unit="USD/hour (synthetic)",
        interval_seconds=TICK_SECONDS,
        services=services,
    )
