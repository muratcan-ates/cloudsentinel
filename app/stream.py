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
import time

from fastapi import APIRouter, HTTPException

from app.detection import load_daily_costs
from app.models import StreamReport, StreamService

router = APIRouter(prefix="/stream", tags=["stream"])

SIM_STREAM_ENV = "SENTINEL_SIM_STREAM"

TICK_SECONDS = 2.5
TREND_LENGTH = 48  # sparkline window the dashboard draws
MAX_CATCHUP_TICKS = 240  # an idle server fast-forwards at most this many
SPIKE_START_CHANCE = 0.005  # per service per tick
SPIKE_TICKS = (6, 14)
WALK_SIGMA = 0.012  # per-tick drift; small keeps the tape calm, not jittery
SPIKE_DRIFT = 0.06
BAND = (0.45, 2.6)  # the walk stays within base × band — no runaways

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
    for record in load_daily_costs():
        by_service.setdefault(record["service"], []).append(record["cost"])
    for service, costs in sorted(by_service.items()):
        base = max(sum(costs) / len(costs) / 24.0, 0.01)
        _lanes[service] = {
            "base": base,
            "rate": base,
            "prev": base,
            "trend": [round(base, 2)],
            "spike": 0,
        }
    for _ in range(TREND_LENGTH):
        _tick_once()


def _tick_once() -> None:
    for lane in _lanes.values():
        lane["prev"] = lane["rate"]
        drift = _rng.gauss(0.0, WALK_SIGMA)
        if lane["spike"] > 0:
            drift += SPIKE_DRIFT
            lane["spike"] -= 1
        elif _rng.random() < SPIKE_START_CHANCE:
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
