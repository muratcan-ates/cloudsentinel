"""Market watch — standing optimisation opportunities against this estate.

The anomaly lanes answer "what changed?". This lane answers the question a
FinOps operator asks on a quiet day: "what is worth doing anyway?" A curated
catalogue of published market moves (commitment discounts, ARM families,
storage tiering, spot capacity, idle sweeps) is matched against the services
the estate actually runs, and each match is costed against that service's own
run rate.

Three disciplines carry over from the rest of the system:

* **Arithmetic, never generation.** A row's saving band is
  ``service run rate × applies_to_share × published reduction band``. The
  catalogue supplies the band and the assumption; Python does the rest. No
  agent touches this lane.
* **Provenance on every row.** Each opportunity ships its source, the date
  the team last checked it, and the assumption it rests on — so a jury (or an
  operator) can argue with the number instead of trusting it.
* **Suggestions, never actions.** Nothing here files an action or touches the
  decision inbox. These are standing opportunities for a human to pick up.

The catalogue is bundled and deterministic. ``SENTINEL_MARKET_FEED_URL``
points the lane at an external catalogue in the same shape (the feeds.py
pattern: TTL cache, single-flight, fall back to the bundled file), which is
how a live market-tracking source would arrive without changing this module.
"""

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app import feeds
from app.detection import load_daily_costs, summarize_costs

logger = logging.getLogger("cloudsentinel.market")

router = APIRouter(prefix="/market", tags=["market"])

MARKET_DATA_FILE = Path(__file__).parent / "data" / "market_watch.json"
MARKET_FEED_ENV = "SENTINEL_MARKET_FEED_URL"

DAYS_PER_MONTH = 30  # same convention the recommender uses for monthly figures


class MarketOpportunity(BaseModel):
    id: str
    headline: str
    category: str
    service: str
    # Monthly band, computed: run rate × share × published reduction band.
    monthly_saving_low: float
    monthly_saving_high: float
    monthly_saving_mid: float
    service_monthly_run_rate: float
    share_of_service_spend: float
    reduction_band: str
    effort: str
    risk: str
    horizon: str
    rationale: str
    watch_out: str
    source: str
    checked: str
    basis: str


class MarketReport(BaseModel):
    source: str  # curated | feed | mock (feed unavailable)
    reviewed: str
    currency: str
    services_matched: int
    opportunity_count: int
    # Gross totals: bands over the same service overlap, so this is an
    # upper bound on a pursuit-everything scenario, not a forecast.
    gross_monthly_low: float
    gross_monthly_high: float
    overlapping_services: list[str]
    opportunities: list[MarketOpportunity]
    note: str


def _validate_catalogue(data: dict) -> dict:
    """Normalise a catalogue payload; malformed entries are dropped, not fatal.

    Mirrors the feeds.py contract discipline: a live catalogue that answers
    the wrong shape must never poison the table, and a lane that cannot
    produce a single valid row is treated as unavailable.
    """
    rows = data.get("opportunities")
    if not isinstance(rows, list):
        raise ValueError("catalogue has no opportunities list")
    kept: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        services = row.get("applies_to")
        if not isinstance(services, list) or not services:
            continue
        try:
            share = float(row["applies_to_share"])
            low = float(row["reduction_min"])
            high = float(row["reduction_max"])
        except (KeyError, TypeError, ValueError):
            continue
        # Shares and reductions are fractions; a catalogue that ships 35
        # instead of 0.35 would invent savings an order of magnitude wide.
        if not (0 < share <= 1 and 0 <= low <= high <= 1):
            continue
        identifier = str(row.get("id") or "").strip()
        headline = str(row.get("headline") or "").strip()
        if not identifier or not headline:
            continue
        kept.append(
            {
                **row,
                "id": identifier,
                "headline": headline,
                "applies_to": [str(s).strip().lower() for s in services if str(s).strip()],
                "applies_to_share": share,
                "reduction_min": low,
                "reduction_max": high,
            }
        )
    dropped = len(rows) - len(kept)
    if dropped:
        logger.warning("market catalogue: dropped %d malformed entr(y/ies)", dropped)
    if not kept:
        raise ValueError("catalogue has no valid opportunities")
    return {
        "note": str(data.get("note") or ""),
        "reviewed": str(data.get("reviewed") or "unknown"),
        "opportunities": kept,
    }


def catalogue_source() -> str:
    return "feed" if os.environ.get(MARKET_FEED_ENV, "").strip() else "curated"


def load_catalogue() -> tuple[dict, str]:
    """Return (catalogue, source-actually-served).

    A configured feed that cannot answer falls back to the bundled file and
    says so, exactly like the cost and security lanes: the badge must never
    claim a live source the operator is not looking at.
    """
    if catalogue_source() == "feed":
        try:
            payload = feeds.fetch_json_feed(
                "market", os.environ[MARKET_FEED_ENV].strip(), _validate_catalogue
            )
            return payload, "feed"
        except feeds.FeedUnavailable:
            logger.warning("market feed unavailable — serving the curated catalogue")
            with MARKET_DATA_FILE.open() as f:
                return _validate_catalogue(json.load(f)), feeds.MOCK_FALLBACK
    with MARKET_DATA_FILE.open() as f:
        return _validate_catalogue(json.load(f)), "curated"


def build_report(min_monthly_saving: float = 0.0) -> MarketReport:
    """Match the catalogue against the estate and cost every hit."""
    records = load_daily_costs()
    summaries = summarize_costs(records)
    run_rates = {
        summary.service.lower(): summary.mean_daily_cost * DAYS_PER_MONTH
        for summary in summaries
    }
    catalogue, served = load_catalogue()

    opportunities: list[MarketOpportunity] = []
    for entry in catalogue["opportunities"]:
        for service in entry["applies_to"]:
            run_rate = run_rates.get(service)
            if run_rate is None:  # the estate does not run this service
                continue
            addressable = run_rate * entry["applies_to_share"]
            low = round(addressable * entry["reduction_min"], 2)
            high = round(addressable * entry["reduction_max"], 2)
            if high < min_monthly_saving:
                continue
            opportunities.append(
                MarketOpportunity(
                    id=entry["id"],
                    headline=entry["headline"],
                    category=str(entry.get("category") or "general"),
                    service=service,
                    monthly_saving_low=low,
                    monthly_saving_high=high,
                    monthly_saving_mid=round((low + high) / 2, 2),
                    service_monthly_run_rate=round(run_rate, 2),
                    share_of_service_spend=entry["applies_to_share"],
                    reduction_band=(
                        f"{round(entry['reduction_min'] * 100)}–"
                        f"{round(entry['reduction_max'] * 100)}%"
                    ),
                    effort=str(entry.get("effort") or "unknown"),
                    risk=str(entry.get("risk") or "unknown"),
                    horizon=str(entry.get("horizon") or "unknown"),
                    rationale=str(entry.get("rationale") or ""),
                    watch_out=str(entry.get("watch_out") or ""),
                    source=str(entry.get("source") or "unattributed"),
                    checked=str(entry.get("checked") or "unknown"),
                    basis=(
                        f"{round(run_rate, 2)}/mo run rate × "
                        f"{round(entry['applies_to_share'] * 100)}% addressable × "
                        f"{round(entry['reduction_min'] * 100)}–"
                        f"{round(entry['reduction_max'] * 100)}% published reduction"
                    ),
                )
            )

    # Biggest credible win first; id keeps the order stable between runs.
    opportunities.sort(key=lambda o: (-o.monthly_saving_mid, o.id, o.service))

    per_service: dict[str, int] = {}
    for opportunity in opportunities:
        per_service[opportunity.service] = per_service.get(opportunity.service, 0) + 1
    overlapping = sorted(s for s, count in per_service.items() if count > 1)

    return MarketReport(
        source=served,
        reviewed=catalogue["reviewed"],
        currency="USD",
        services_matched=len(per_service),
        opportunity_count=len(opportunities),
        gross_monthly_low=round(sum(o.monthly_saving_low for o in opportunities), 2),
        gross_monthly_high=round(sum(o.monthly_saving_high for o in opportunities), 2),
        overlapping_services=overlapping,
        opportunities=opportunities,
        note=(
            "Standing opportunities, not anomalies: published market bands costed "
            "against this estate's own run rate. Bands over the same service "
            "overlap, so the gross total is an upper bound on pursuing everything "
            "— never a forecast. Nothing here files an action."
        ),
    )


@router.get("/opportunities")
def get_market_opportunities(
    min_monthly_saving: float = Query(
        0.0,
        ge=0,
        allow_inf_nan=False,
        description=(
            "Drop opportunities whose upper band falls below this monthly "
            "figure — the operator's 'don't show me pocket change' knob."
        ),
    ),
) -> MarketReport:
    """Rank standing cost opportunities for the services this estate runs."""
    return build_report(min_monthly_saving)
